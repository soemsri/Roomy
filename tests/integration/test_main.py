import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sys
import os

# Ensure current directory is in path
sys.path.append(os.path.dirname(__file__))

from database import Base, get_db
import security

from main import app, ADMIN_PASSWORD
import models

# Use a separate test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_suk_anan.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

client = TestClient(app)

@pytest.fixture()
def setup_db():
    # Clear all overrides to prevent leakage
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    
    # Create default owner for tests
    db = TestingSessionLocal()
    hashed_pw = security.hash_password("admin1234")
    owner = models.Owner(line_user_id="UADMIN", password_hash=hashed_pw, session_token="test_session_token")
    db.add(owner)
    db.commit()
    db.close()
    
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()

def get_admin_cookie():
    db = TestingSessionLocal()
    owner = db.query(models.Owner).first()
    cookie = {"admin_session": owner.session_token}
    db.close()
    return cookie

def test_root(setup_db):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "SukAnan Apartment API is running"}

def test_unauthorized_admin_access(setup_db):
    response = client.get("/admin/dashboard")
    assert response.status_code == 401

def test_add_room(setup_db):
    cookies = get_admin_cookie()
    response = client.post("/admin/rooms/add", data={
        "room_number": "B201",
        "floor": 2,
        "base_rent": 4000,
        "electricity_rate": 8,
        "water_rate": 18
    }, cookies=cookies)
    assert response.status_code == 200
    assert response.json() == {"status": "Success"}

def test_resident_management(setup_db):
    cookies = get_admin_cookie()
    # 1. Add Room
    client.post("/admin/rooms/add", data={
        "room_number": "B202", "floor": 2, "base_rent": 4000, "electricity_rate": 8, "water_rate": 18
    }, cookies=cookies)
    
    db = TestingSessionLocal()
    room = db.query(models.Room).filter(models.Room.room_number == "B202").first()
    tenant = models.Tenant(line_user_id="U12345", current_room_id=room.id, status="Active")
    db.add(tenant)
    db.commit()
    tenant_id = tenant.id
    
    # 3. Add Resident
    response = client.post(f"/admin/tenants/{tenant_id}/residents/add", data={
        "nickname": "Somchai",
        "first_name": "Somchai",
        "last_name": "Saetang"
    }, cookies=cookies)
    assert response.status_code == 200
    
    # 4. List Residents
    response = client.get(f"/admin/tenants/{tenant_id}/residents", cookies=cookies)
    assert response.status_code == 200
    residents = response.json()
    assert len(residents) == 1
    assert residents[0]["nickname"] == "Somchai"
    resident_id = residents[0]["id"]
    
    # 5. Edit Resident
    response = client.post(f"/admin/residents/{resident_id}/edit", data={
        "nickname": "Somchai (Edit)",
        "first_name": "Somchai",
        "last_name": "Saetang"
    }, cookies=cookies)
    assert response.status_code == 200
    
    # 6. Delete Resident Constraint (Last one)
    response = client.post(f"/admin/residents/{resident_id}/delete", cookies=cookies)
    assert response.status_code == 400
    assert "ต้องมีอย่างน้อย 1 รายชื่อ" in response.json()["detail"]
    
    # 7. Add second resident and delete first
    client.post(f"/admin/tenants/{tenant_id}/residents/add", data={"nickname": "Somsri"}, cookies=cookies)
    response = client.post(f"/admin/residents/{resident_id}/delete", cookies=cookies)
    assert response.status_code == 200
    
    db.close()

def test_meter_and_billing(setup_db):
    cookies = get_admin_cookie()
    # 1. Add Room
    client.post("/admin/rooms/add", data={
        "room_number": "C301", "floor": 3, "base_rent": 3000, "electricity_rate": 8, "water_rate": 18
    }, cookies=cookies)
    
    db = TestingSessionLocal()
    room = db.query(models.Room).filter(models.Room.room_number == "C301").first()
    tenant = models.Tenant(line_user_id="U67890", current_room_id=room.id, status="Active")
    db.add(tenant)
    db.commit()
    room_id = room.id
    db.close()
    
    # 2. Record Meter
    response = client.post(
        "/admin/meters/record", 
        data={
            "room_id": room_id,
            "month": 5,
            "year": 2026,
            "elec": 100,
            "water": 20
        }, 
        cookies=cookies
    )
    assert response.status_code == 200
    assert response.json()["invoice_uuid"] is not None

def test_bill_not_found(setup_db):
    response = client.get("/bill/non_existent_uuid")
    assert response.status_code == 404
    assert "ไม่พบข้อมูลใบแจ้งหนี้" in response.text
    
    response_en = client.get("/bill/non_existent_uuid?lang=en")
    assert response_en.status_code == 404
    assert "Invoice Not Found" in response_en.text

    response_jp = client.get("/bill/non_existent_uuid?lang=jp")
    assert response_jp.status_code == 404
    assert "請求書が見つかりません" in response_jp.text


def test_confirm_cash_payment_notification(setup_db):
    from unittest.mock import MagicMock
    import main
    
    # 1. Mock tenant_bot_api and BASE_URL
    main.tenant_bot_api = MagicMock()
    main.BASE_URL = "https://mockurl.com"
    
    # 2. Add Room & Tenant
    cookies = get_admin_cookie()
    client.post("/admin/rooms/add", data={
        "room_number": "D401", "floor": 4, "base_rent": 5000, "electricity_rate": 8, "water_rate": 18
    }, cookies=cookies)
    
    db = TestingSessionLocal()
    room = db.query(models.Room).filter(models.Room.room_number == "D401").first()
    
    tenant = models.Tenant(
        line_user_id="U_MOCK_TENANT_123", 
        current_room_id=room.id, 
        status="Active", 
        language="th",
        full_name="Mock Tenant"
    )
    db.add(tenant)
    db.commit()
    
    # 3. Create an Initial Move-in Invoice for the tenant
    invoice = models.Invoice(
        room_id=room.id,
        tenant_id=tenant.id,
        billing_month=5,
        billing_year=2026,
        rent_amount=5000.0,
        electricity_amount=0.0,
        water_amount=0.0,
        total_amount=5000.0,
        status="Unpaid",
        invoice_type="Initial",
        uuid="mock-invoice-uuid-12345"
    )
    db.add(invoice)
    db.commit()
    invoice_id = invoice.id
    db.close()
    
    # 4. Invoke Confirm Cash Payment POST endpoint
    file_content = b"fake receipt image"
    files = {"image": ("receipt.png", file_content, "image/png")}
    
    response = client.post(
        f"/admin/invoice/{invoice_id}/confirm-cash",
        files=files,
        cookies=cookies
    )
    
    assert response.status_code == 200
    assert response.json()["status"] == "Success"
    assert "receipt" in response.json()
    
    # 5. Verify database updates
    db = TestingSessionLocal()
    updated_inv = db.query(models.Invoice).filter(models.Invoice.id == invoice_id).first()
    assert updated_inv.status == "Paid"
    assert updated_inv.payment_method == "Cash"
    assert updated_inv.payment_receipt_img is not None
    db.close()
    
    # 6. Verify that tenant_bot_api.push_message was invoked to send receipt and welcome messages
    assert main.tenant_bot_api.push_message.call_count == 2
    
    # First call is for Flex Receipt Message
    first_call_args = main.tenant_bot_api.push_message.call_args_list[0][0][0]
    assert first_call_args.to == "U_MOCK_TENANT_123"
    assert len(first_call_args.messages) == 1
    assert first_call_args.messages[0].alt_text == "ใบเสร็จรับเงิน"
    
    # Second call is for Initial Move-in Welcome Message
    second_call_args = main.tenant_bot_api.push_message.call_args_list[1][0][0]
    assert second_call_args.to == "U_MOCK_TENANT_123"
    assert len(second_call_args.messages) == 1
    assert "ยินดีต้อนรับ" in second_call_args.messages[0].text or "Welcome" in second_call_args.messages[0].text or "✅" in second_call_args.messages[0].text

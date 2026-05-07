import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sys
import os

# Ensure current directory is in path
sys.path.append(os.path.dirname(__file__))

from database import Base, get_db

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
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.clear()

def get_admin_cookie():
    return {"admin_session": ADMIN_PASSWORD}

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

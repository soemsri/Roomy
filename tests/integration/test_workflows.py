import pytest
import json
import models

def test_tenant_registration_and_move_in_approval(client, db_session):
    cookies = {"admin_session": "test_session_token"}
    
    # 1. Setup Room
    client.post("/admin/rooms/add", data={
        "room_number": "E501", "floor": 5, "base_rent": 6000, "electricity_rate": 8.0, "water_rate": 18.0
    }, cookies=cookies)
    
    room = db_session.query(models.Room).filter(models.Room.room_number == "E501").first()
    
    # 2. Setup Building
    building = models.Building(name="Building A")
    db_session.add(building)
    db_session.commit()
    
    # 3. Simulate tenant registration
    tenant = models.Tenant(
        line_user_id="U_FLOW_TEST_1",
        full_name="Workflow Tenant",
        phone_number="0812345678",
        citizen_id="1234567890123",
        status="Pending",
        current_room_id=room.id
    )
    db_session.add(tenant)
    db_session.commit()
    tenant_id = tenant.id
    
    # 4. Admin issues/approves registration and sends initial bill
    response = client.post(f"/admin/registration/{tenant_id}/request-payment", data={
        "room_ids": str(room.id)
    }, cookies=cookies)
    
    assert response.status_code == 200
    assert response.json() == {"status": "Success"}
    
    # 5. Verify tenant status is changed to "Awaiting Payment"
    db_session.refresh(tenant)
    assert tenant.status == "Awaiting Payment"
    
    # 6. Verify Initial Invoice was created
    invoice = db_session.query(models.Invoice).filter(
        models.Invoice.tenant_id == tenant_id,
        models.Invoice.invoice_type == "Initial"
    ).first()
    
    assert invoice is not None
    assert invoice.status == "Unpaid"
    # total amount should reflect security deposit (5000) + 1 month advance rent (6000) = 11000
    assert invoice.total_amount == 11000.0

def test_confirm_cash_payment_notification_flow(client, db_session):
    cookies = {"admin_session": "test_session_token"}
    
    # Setup Room, Tenant, and Invoice
    client.post("/admin/rooms/add", data={
        "room_number": "E502", "floor": 5, "base_rent": 6000, "electricity_rate": 8.0, "water_rate": 18.0
    }, cookies=cookies)
    
    room = db_session.query(models.Room).filter(models.Room.room_number == "E502").first()
    
    tenant = models.Tenant(
        line_user_id="U_FLOW_TEST_2",
        full_name="Cash Workflow Tenant",
        phone_number="0819999999",
        status="Awaiting Payment",
        current_room_id=room.id,
        language="th"
    )
    db_session.add(tenant)
    db_session.commit()
    
    invoice = models.Invoice(
        room_id=room.id,
        tenant_id=tenant.id,
        billing_month=5,
        billing_year=2026,
        rent_amount=6000.0,
        electricity_amount=0.0,
        water_amount=0.0,
        total_amount=11000.0,
        status="Unpaid",
        invoice_type="Initial",
        uuid="mock-workflow-invoice-uuid"
    )
    db_session.add(invoice)
    db_session.commit()
    invoice_id = invoice.id
    
    # Submit cash payment confirm
    file_content = b"workflow receipt bytes"
    files = {"image": ("receipt.jpg", file_content, "image/jpeg")}
    
    response = client.post(
        f"/admin/invoice/{invoice_id}/confirm-cash",
        files=files,
        cookies=cookies
    )
    
    assert response.status_code == 200
    assert response.json()["status"] == "Success"
    
    # Verify invoice paid status in DB
    db_session.refresh(invoice)
    assert invoice.status == "Paid"
    assert invoice.payment_method == "Cash"
    assert invoice.payment_receipt_img is not None
    
    # Verify tenant is now Active and room is Occupied (final approval triggered)
    db_session.refresh(tenant)
    assert tenant.status == "Active"
    
    db_session.refresh(room)
    assert room.status == "Occupied"

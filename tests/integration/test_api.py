import pytest
import models

def test_unauthorized_dashboard_access(client):
    response = client.get("/admin/dashboard")
    assert response.status_code == 401

def test_add_room_success(client):
    cookies = {"admin_session": "test_session_token"}
    response = client.post("/admin/rooms/add", data={
        "room_number": "A101",
        "floor": 1,
        "base_rent": 5000,
        "electricity_rate": 8.0,
        "water_rate": 18.0
    }, cookies=cookies)
    
    assert response.status_code == 200
    assert response.json() == {"status": "Success"}

def test_resident_management_workflow(client, db_session):
    cookies = {"admin_session": "test_session_token"}
    
    # 1. Add Room
    client.post("/admin/rooms/add", data={
        "room_number": "A102", "floor": 1, "base_rent": 5000, "electricity_rate": 8.0, "water_rate": 18.0
    }, cookies=cookies)
    
    room = db_session.query(models.Room).filter(models.Room.room_number == "A102").first()
    assert room is not None
    
    # 2. Add Tenant
    tenant = models.Tenant(line_user_id="U_INTEG_TEST_1", current_room_id=room.id, status="Active")
    db_session.add(tenant)
    db_session.commit()
    tenant_id = tenant.id
    
    # 3. Add Resident (Thasapol)
    response = client.post(f"/admin/tenants/{tenant_id}/residents/add", data={
        "nickname": "Tee",
        "first_name": "Thasapol",
        "last_name": "Saetang"
    }, cookies=cookies)
    assert response.status_code == 200
    
    # 4. List Residents
    response = client.get(f"/admin/tenants/{tenant_id}/residents", cookies=cookies)
    assert response.status_code == 200
    res_list = response.json()
    assert len(res_list) == 1
    assert res_list[0]["nickname"] == "Tee"
    res_id = res_list[0]["id"]
    
    # 5. Edit Resident
    response = client.post(f"/admin/residents/{res_id}/edit", data={
        "nickname": "Tee (Updated)",
        "first_name": "Thasapol",
        "last_name": "Saetang"
    }, cookies=cookies)
    assert response.status_code == 200
    
    # 6. Try deleting the ONLY resident (should be blocked)
    response = client.post(f"/admin/residents/{res_id}/delete", cookies=cookies)
    assert response.status_code == 400
    assert "ต้องมีอย่างน้อย 1 รายชื่อ" in response.json()["detail"]
    
    # 7. Add second resident
    response = client.post(f"/admin/tenants/{tenant_id}/residents/add", data={
        "nickname": "Somsri",
        "first_name": "Somsri",
        "last_name": "Sukjai"
    }, cookies=cookies)
    assert response.status_code == 200
    
    # 8. Now delete the first resident (should succeed)
    response = client.post(f"/admin/residents/{res_id}/delete", cookies=cookies)
    assert response.status_code == 200

def test_meter_recording_and_billing(client, db_session):
    cookies = {"admin_session": "test_session_token"}
    
    # Add room
    client.post("/admin/rooms/add", data={
        "room_number": "A103", "floor": 1, "base_rent": 5000, "electricity_rate": 8.0, "water_rate": 18.0
    }, cookies=cookies)
    
    room = db_session.query(models.Room).filter(models.Room.room_number == "A103").first()
    
    # Add Tenant
    tenant = models.Tenant(line_user_id="U_INTEG_TEST_2", current_room_id=room.id, status="Active")
    db_session.add(tenant)
    db_session.commit()
    
    # Record meter
    response = client.post("/admin/meters/record", data={
        "room_id": room.id,
        "month": 5,
        "year": 2026,
        "elec": 150,
        "water": 30
    }, cookies=cookies)
    
    assert response.status_code == 200
    invoice_uuid = response.json()["invoice_uuid"]
    assert invoice_uuid is not None
    
    # Verify invoice was created
    invoice = db_session.query(models.Invoice).filter(models.Invoice.uuid == invoice_uuid).first()
    assert invoice is not None
    assert invoice.electricity_reading == 150.0
    assert invoice.water_reading == 30.0
    assert invoice.status == "Draft"

import os
import sys

# Standard imports
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import Base
from database import Base

# Then import others
from main import app, ADMIN_PASSWORD, get_db
import models

# Use a separate test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_manual.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

def run_tests():
    print("Starting System Validation...")
    
    # Init DB
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    cookies = {"admin_session": ADMIN_PASSWORD}
    
    # 1. Add Room
    print("Testing: Add Room...")
    res = client.post("/admin/rooms/add", data={
        "room_number": "T101", "floor": 1, "base_rent": 3000, "electricity_rate": 8, "water_rate": 18
    }, cookies=cookies)
    assert res.status_code == 200
    
    # 2. Setup Tenant
    print("Testing: Setup Tenant...")
    db = TestingSessionLocal()
    room = db.query(models.Room).filter(models.Room.room_number == "T101").first()
    tenant = models.Tenant(line_user_id="LINE_USER_1", current_room_id=room.id, status="Active")
    db.add(tenant)
    db.commit()
    tenant_id = tenant.id
    
    # 3. Add Resident
    print("Testing: Add Resident...")
    res = client.post(f"/admin/tenants/{tenant_id}/residents/add", data={
        "nickname": "TestUser", "first_name": "Test", "last_name": "User"
    }, cookies=cookies)
    assert res.status_code == 200
    
    # 4. List Residents
    print("Testing: List Residents...")
    res = client.get(f"/admin/tenants/{tenant_id}/residents", cookies=cookies)
    residents = res.json()
    assert len(residents) == 1
    assert residents[0]["nickname"] == "TestUser"
    resident_id = residents[0]["id"]
    
    # 5. Edit Resident
    print("Testing: Edit Resident...")
    res = client.post(f"/admin/residents/{resident_id}/edit", data={
        "nickname": "TestUser-Edited", "first_name": "Test", "last_name": "User"
    }, cookies=cookies)
    assert res.status_code == 200
    
    # 6. Delete Resident Constraint
    print("Testing: Delete Resident Constraint (Should Fail)...")
    res = client.post(f"/admin/residents/{resident_id}/delete", cookies=cookies)
    assert res.status_code == 400
    print(f"  Got expected error: {res.json()['detail']}")
    
    # 7. Add another and delete first
    print("Testing: Delete Resident (Should Succeed with 2 residents)...")
    client.post(f"/admin/tenants/{tenant_id}/residents/add", data={"nickname": "SecondUser"}, cookies=cookies)
    res = client.post(f"/admin/residents/{resident_id}/delete", cookies=cookies)
    assert res.status_code == 200
    
    # 8. Meter Reading
    print("Testing: Meter Reading & Billing...")
    res = client.post("/admin/meters/record", data={
        "room_id": room.id,
        "month": 5,
        "year": 2026,
        "elec": 150,
        "water": 25,
        "issue_bill": True
    }, cookies=cookies)
    assert res.status_code == 200
    print(f"  Invoice created: {res.json()['invoice_uuid']}")

    print("\nALL TESTS PASSED SUCCESSFULLY!")
    db.close()

if __name__ == "__main__":
    try:
        run_tests()
    finally:
        if os.path.exists("./test_manual.db"):
            os.remove("./test_manual.db")

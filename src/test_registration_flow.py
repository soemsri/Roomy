import os
import sys
import uuid
from datetime import datetime

# Ensure src is in path
sys.path.append(os.path.dirname(__file__))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock

# Standard imports for DB
from database import Base, get_db

# Use a separate test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_registration.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# LOAD MODELS
import models
import main

from main import app, ADMIN_PASSWORD, handle_admin_message, handle_tenant_message

# Mock LINE Event
class MockMessage:
    def __init__(self, text):
        self.text = text

class MockSource:
    def __init__(self, user_id):
        self.user_id = user_id

class MockEvent:
    def __init__(self, user_id, text, reply_token="token"):
        self.source = MockSource(user_id)
        self.message = MockMessage(text)
        self.reply_token = reply_token

def run_registration_tests():
    # Use localized overrides and mocks
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    
    # Mock LINE API
    main.admin_bot_api = MagicMock()
    main.tenant_bot_api = MagicMock()
    main.line_bot_api = MagicMock()

    print("Starting Multi-step Registration Validation...")
    
    # Init DB
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    
    # Setup Data
    owner_id = "OWNER_LINE"
    db.add(models.Owner(line_user_id=owner_id, display_name="Owner"))
    db.add(models.Room(room_number="R101", floor=1, base_rent=3000, status="Vacant"))
    db.commit()
    
    tenant_line_id = "TENANT_LINE"
    
    # 0. Initial Message (Triggers status -> AwaitingRoom)
    print("Step 0: Initial Greeting...")
    handle_tenant_message(MockEvent(tenant_line_id, "สวัสดี"), db=db)
    tenant = db.query(models.Tenant).filter(models.Tenant.line_user_id == tenant_line_id).first()
    assert tenant.status == "AwaitingRoom"

    # 1. Step 1: Enter Room Number
    print("Step 1: Tenant enters Room Number 'R101'...")
    handle_tenant_message(MockEvent(tenant_line_id, "R101"), db=db)
    
    db.refresh(tenant)
    assert tenant is not None
    assert tenant.status == "AwaitingName"
    assert tenant.current_room_id is not None
    print("  SUCCESS: Status is AwaitingName")

    # 2. Step 2: Enter Name
    print("Step 2: Tenant enters Name 'John Doe'...")
    handle_tenant_message(MockEvent(tenant_line_id, "John Doe"), db=db)
    db.refresh(tenant)
    assert tenant.full_name == "John Doe"
    assert tenant.status == "AwaitingPhone"
    print("  SUCCESS: Status is AwaitingPhone")

    # 3. Step 3: Enter Phone
    print("Step 3: Tenant enters Phone '0812345678'...")
    handle_tenant_message(MockEvent(tenant_line_id, "0812345678"), db=db)
    db.refresh(tenant)
    assert tenant.phone_number == "0812345678"
    assert tenant.status == "Pending"
    print("  SUCCESS: Status is Pending")

    # 4. Step 4: Owner Approves
    print("Step 4: Owner approves via Dashboard...")
    cookies = {"admin_session": ADMIN_PASSWORD}
    res = client.post(f"/admin/registration/{tenant.id}/approve", cookies=cookies)
    assert res.status_code == 200
    
    db.refresh(tenant)
    room = db.query(models.Room).filter(models.Room.room_number == "R101").first()
    assert tenant.status == "Active"
    assert room.status == "Occupied"
    
    lease = db.query(models.Lease).filter(models.Lease.tenant_id == tenant.id).first()
    assert lease is not None
    print("  SUCCESS: Tenant is Active, Room is Occupied, Lease created.")

    print("\nALL REGISTRATION FLOW TESTS PASSED!")
    db.close()

if __name__ == "__main__":
    try:
        run_registration_tests()
    finally:
        if os.path.exists("./test_registration.db"):
            try:
                os.remove("./test_registration.db")
            except: pass

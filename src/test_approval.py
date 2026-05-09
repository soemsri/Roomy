import os
import sys
import uuid
from datetime import datetime

# Ensure src is in path
sys.path.append(os.path.dirname(__file__))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Standard imports for DB
from database import Base, get_db

# Use a separate test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_approval.db"
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

def run_approval_tests():
    # Use localized overrides and mocks
    import main
    from unittest.mock import MagicMock
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    main.admin_bot_api = MagicMock()
    main.tenant_bot_api = MagicMock()

    print("Starting Approval Workflow Validation...")
    
    # Init DB
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    
    # 1. Setup Data
    print("Step 1: Setup Owner and Room...")
    owner_id = "LINE_OWNER_123"
    db.add(models.Owner(line_user_id=owner_id, display_name="Owner"))
    db.add(models.Room(room_number="R999", floor=9, base_rent=5000, status="Vacant"))
    db.commit()
    
    # 2. Simulate Tenant Registration Message
    print("Step 2: Simulating Tenant Registration ('R999')...")
    tenant_line_id = "LINE_TENANT_456"
    
    # 2.0 Initial Greeting
    handle_tenant_message(MockEvent(tenant_line_id, "Hello"), db=db)
    
    event = MockEvent(tenant_line_id, "R999")
    
    # Manually call handle_tenant_message
    handle_tenant_message(event, db=db)
    
    # Verify Pending Status
    tenant = db.query(models.Tenant).filter(models.Tenant.line_user_id == tenant_line_id).first()
    assert tenant is not None
    assert tenant.status == "AwaitingName"
    
    # Complete registration steps
    handle_tenant_message(MockEvent(tenant_line_id, "Test Tenant"), db=db)
    handle_tenant_message(MockEvent(tenant_line_id, "0812345678"), db=db)
    
    db.refresh(tenant)
    assert tenant.status == "Pending"
    
    room = db.query(models.Room).filter(models.Room.room_number == "R999").first()
    assert room.status == "Vacant" 
    print("  SUCCESS: Tenant is Pending, Room is still Vacant.")

    # 3. Simulate Dashboard View
    print("Step 3: Checking Dashboard for Pending Registration...")
    cookies = {"admin_session": ADMIN_PASSWORD}
    res = client.get("/admin/dashboard", cookies=cookies)
    assert res.status_code == 200
    assert "Pending Registrations" in res.text
    print("  SUCCESS: Dashboard shows pending request.")

    # 4. Simulate Owner Approval via LINE Button
    print("Step 4: Simulating Owner clicking 'Approve' button...")
    approve_text = f"APPROVE_REG_{tenant.id}"
    approval_event = MockEvent(owner_id, approve_text)
    
    handle_admin_message(approval_event, db=db)
    
    # Re-fetch and Verify
    db.refresh(tenant)
    db.refresh(room)
    assert tenant.status == "Active"
    assert room.status == "Occupied"
    
    # Check Lease creation
    lease = db.query(models.Lease).filter(models.Lease.tenant_id == tenant.id).first()
    assert lease is not None
    print("  SUCCESS: Tenant Approved, Room Occupied, Lease Created.")

    print("\nALL APPROVAL WORKFLOW TESTS PASSED!")
    db.close()

if __name__ == "__main__":
    try:
        run_approval_tests()
    finally:
        if os.path.exists("./test_approval.db"):
            try:
                os.remove("./test_approval.db")
            except: pass

import os
import sys
import uuid
from datetime import datetime
from unittest.mock import MagicMock

# Ensure src is in path
sys.path.append(os.path.dirname(__file__))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Standard imports for DB
from database import Base, get_db

# Use a separate test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_dual.db"
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

def run_dual_channel_tests():
    # Use localized overrides and mocks
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    main.admin_bot_api = MagicMock()
    main.tenant_bot_api = MagicMock()

    print("Starting Dual-Channel System Validation...")
    
    # Init DB
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    
    # Setup Data
    admin_line_id = "ADMIN_USER_ID"
    tenant_line_id = "TENANT_USER_ID"
    db.add(models.Owner(line_user_id=admin_line_id, display_name="Owner"))
    db.add(models.Room(room_number="101", floor=1, base_rent=3000, status="Vacant"))
    db.commit()
    
    # --- TEST TENANT CHANNEL ---
    print("\n--- Testing Tenant Channel ---")
    
    print("Step 1: Tenant sends room number to Tenant Channel...")
    handle_tenant_message(MockEvent(tenant_line_id, "101"), db=db)
    tenant = db.query(models.Tenant).filter(models.Tenant.line_user_id == tenant_line_id).first()
    assert tenant.status == "AwaitingName"
    print("  SUCCESS: Tenant status is AwaitingName")

    print("Step 2: Tenant sends name...")
    handle_tenant_message(MockEvent(tenant_line_id, "John Doe"), db=db)
    db.refresh(tenant)
    assert tenant.status == "AwaitingPhone"
    print("  SUCCESS: Tenant status is AwaitingPhone")

    print("Step 3: Tenant sends phone...")
    handle_tenant_message(MockEvent(tenant_line_id, "0899999999"), db=db)
    db.refresh(tenant)
    assert tenant.status == "Pending"
    # Verify push message was sent to ADMIN channel
    main.admin_bot_api.push_message.assert_called()
    print("  SUCCESS: Tenant status is Pending and Admin was notified")

    # --- TEST ADMIN CHANNEL ---
    print("\n--- Testing Admin Channel ---")
    
    print("Step 4: Admin sends 'ผังห้อง' command...")
    handle_admin_message(MockEvent(admin_line_id, "ผังห้อง"), db=db)
    main.admin_bot_api.reply_message.assert_called()
    args, kwargs = main.admin_bot_api.reply_message.call_args
    assert "/admin/dashboard" in str(args[1])
    print("  SUCCESS: Admin received dashboard link")

    print("Step 5: Admin approves tenant via command...")
    approve_cmd = f"APPROVE_REG_{tenant.id}"
    handle_admin_message(MockEvent(admin_line_id, approve_cmd), db=db)
    
    db.refresh(tenant)
    room = db.query(models.Room).filter(models.Room.room_number == "101").first()
    assert tenant.status == "Active"
    assert room.status == "Occupied"
    # Verify notification was sent to TENANT channel
    main.tenant_bot_api.push_message.assert_called()
    print("  SUCCESS: Admin approved registration, Tenant is Active, Room is Occupied")

    print("\nDUAL-CHANNEL TESTS PASSED SUCCESSFULLY!")
    db.close()

if __name__ == "__main__":
    try:
        run_dual_channel_tests()
    finally:
        if os.path.exists("./test_dual.db"):
            try:
                os.remove("./test_dual.db")
            except: pass

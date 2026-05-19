import os
import sys
import uuid
import json
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

def test_registration_flow():
    # Use localized overrides and mocks
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    
    # Mock LINE API
    main.admin_bot_api = MagicMock()
    main.tenant_bot_api = MagicMock()
    main.line_bot_api = MagicMock()

    # Init DB
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    
    # Setup Data
    owner_id = "OWNER_LINE"
    owner = models.Owner(
        line_user_id=owner_id, 
        display_name="Owner",
        lease_template="Contract for {tenant_name} room {room_number}",
        move_in_fees_config=json.dumps([
            {"name": "เงินประกัน", "value": 1, "is_multiplier": True},
            {"name": "ค่าเช่าล่วงหน้า", "value": 1, "is_multiplier": True}
        ])
    )
    db.add(owner)
    db.add(models.Room(room_number="R101", floor=1, base_rent=3000, status="Vacant"))
    db.commit()
    
    tenant_line_id = "TENANT_LINE"
    
    # 0. Initial Message (Triggers status -> AwaitingRegistration)
    handle_tenant_message(MockEvent(tenant_line_id, "สวัสดี"), db=db)
    tenant = db.query(models.Tenant).filter(models.Tenant.line_user_id == tenant_line_id).first()
    assert tenant.status == "AwaitingRegistration"

    # 1. Step 1: Submit Registration via API
    registration_data = {
        "full_name": "John Doe",
        "phone_number": "0812345678",
        "citizen_id": "1234567890123",
        "requested_move_in_date": "2026-06-01"
    }
    res = client.post(f"/register/{tenant.uuid}", json=registration_data)
    assert res.status_code == 200
    
    db.refresh(tenant)
    assert tenant.full_name == "John Doe"
    assert tenant.status == "Pending"

    # 2. Step 2: Owner Approves
    # We need to simulate the login or bypass security if needed
    owner = db.query(models.Owner).first()
    owner.password_hash = "fake_hash"
    db.commit()
    
    room = db.query(models.Room).filter(models.Room.room_number == "R101").first()
    
    cookies = {"admin_session": "fake_hash"}
    res = client.post(f"/admin/registration/{tenant.id}/approve", data={"room_ids": str(room.id)}, cookies=cookies)
    assert res.status_code == 200
    
    db.refresh(tenant)
    db.refresh(room)
    assert tenant.status == "Active"
    assert room.status == "Occupied"
    assert tenant.current_room_id == room.id
    
    lease = db.query(models.Lease).filter(models.Lease.tenant_id == tenant.id).first()
    assert lease is not None
    db.close()

if __name__ == "__main__":
    test_registration_flow()
    if os.path.exists("./test_registration.db"):
        try:
            os.remove("./test_registration.db")
        except: pass

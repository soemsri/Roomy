import os
import sys
import uuid
from datetime import datetime

# Ensure src is in path
sys.path.append(os.path.dirname(__file__))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import EVERYTHING manually to ensure fresh load
import database
import models

# Use a separate test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_final_approval.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Overwrite database.Base and models with same metadata if needed
# but here we just create tables on the specific engine
models.Base.metadata.drop_all(bind=engine)
models.Base.metadata.create_all(bind=engine)

from main import app, ADMIN_PASSWORD

app.dependency_overrides[database.get_db] = override_get_db
client = TestClient(app)

def run_tests():
    print("Starting Final Approval Test...")
    db = TestingSessionLocal()
    
    # 1. Setup
    from security import hash_password
    pw_hash = hash_password(ADMIN_PASSWORD)
    owner = models.Owner(
        line_user_id="OWNER_LINE", 
        display_name="Owner", 
        password_hash=pw_hash,
        lease_template="Contract for {tenant_name} Room {room_number}"
    )
    room = models.Room(room_number="A001", floor=1, base_rent=3000, status="Vacant")
    db.add(owner)
    db.add(room)
    db.commit()
    
    # 2. Simulate Registration (Creating Pending Tenant)
    print("Step 1: Creating Pending Tenant...")
    tenant = models.Tenant(
        line_user_id="TENANT_LINE", 
        full_name="John Doe",
        current_room_id=room.id, 
        status="Pending", 
        uuid=str(uuid.uuid4())
    )
    db.add(tenant)
    db.commit()
    tenant_id = tenant.id
    
    # 3. Verify Dashboard shows it
    print("Step 2: Verifying Dashboard...")
    cookies = {"admin_session": pw_hash}
    res = client.get("/admin/dashboard", cookies=cookies)
    assert res.status_code == 200
    assert "Pending Registrations" in res.text
    assert "A001" in res.text
    print("  SUCCESS: Dashboard displays pending tenant.")
    
    # 4. Approve via API
    print("Step 3: Approving via API...")
    res = client.post(f"/admin/registration/{tenant_id}/approve", data={"room_ids": str(room.id)}, cookies=cookies)
    assert res.status_code == 200
    
    # 5. Verify status change
    db.refresh(tenant)
    db.refresh(room)
    assert tenant.status == "Active"
    assert room.status == "Occupied"
    
    # Check lease
    lease = db.query(models.Lease).filter(models.Lease.tenant_id == tenant_id).first()
    assert lease is not None
    print("  SUCCESS: Tenant Active, Room Occupied, Lease Created.")
    
    # 6. Reject Test
    print("Step 4: Reject Test (New Request)...")
    room2 = models.Room(room_number="A002", floor=1, base_rent=3000, status="Vacant")
    db.add(room2)
    db.commit()
    tenant2 = models.Tenant(line_user_id="TENANT_LINE_2", current_room_id=room2.id, status="Pending", uuid=str(uuid.uuid4()))
    db.add(tenant2)
    db.commit()
    
    res = client.post(f"/admin/registration/{tenant2.id}/reject", cookies=cookies)
    assert res.status_code == 200
    
    db.refresh(tenant2)
    assert tenant2.status == "Rejected"
    assert tenant2.current_room_id is None
    print("  SUCCESS: Rejection handled correctly.")

    print("\nALL WORKFLOW TESTS PASSED!")
    db.close()

if __name__ == "__main__":
    try:
        run_tests()
    finally:
        if os.path.exists("./test_final_approval.db"):
            try:
                os.remove("./test_final_approval.db")
            except: pass

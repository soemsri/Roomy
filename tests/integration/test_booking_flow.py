import os
import sys
import uuid
from datetime import datetime
from unittest.mock import MagicMock

# Ensure test and src directories are in path
sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
import models
import main
from main import app, handle_tenant_message

# Setup isolated test database
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_booking.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

# Mock LINE Event
class MockMessage:
    def __init__(self, text):
        self.text = text

class MockSource:
    def __init__(self, user_id):
        self.user_id = user_id

class MockEvent:
    def __init__(self, user_id, text, reply_token="token_123"):
        self.source = MockSource(user_id)
        self.message = MockMessage(text)
        self.reply_token = reply_token

def setup_module(module):
    if os.path.exists("./test_booking.db"):
        try:
            os.remove("./test_booking.db")
        except Exception:
            pass
    Base.metadata.create_all(bind=engine)

    # Seed owner, building and a test room
    db = TestingSessionLocal()
    owner = models.Owner(
        line_user_id="U_TEST_ADMIN_LINE_ID",
        display_name="SukAnan Apartment Test",
        lease_template="สัญญาเช่าทดสอบ: ข้อ 1 ผู้เช่าต้องชำระค่าเช่าตรงเวลา"
    )
    building = models.Building(name="อาคาร A", description="อาคารทดสอบ")
    db.add(owner)
    db.add(building)
    db.commit()

    room = models.Room(
        building_id=building.id,
        room_number="101",
        floor=1,
        status="Vacant",
        base_rent=3500.0,
        electricity_rate=8.0,
        water_rate=18.0
    )
    db.add(room)
    db.commit()
    db.close()

def teardown_module(module):
    engine.dispose()
    if os.path.exists("./test_booking.db"):
        try:
            os.remove("./test_booking.db")
        except Exception:
            pass

def test_booking_workflow():
    app.dependency_overrides[get_db] = override_get_db
    from controllers.admin import get_current_user
    app.dependency_overrides[main.get_admin] = lambda: True
    app.dependency_overrides[main.get_super_admin] = lambda: True
    app.dependency_overrides[get_current_user] = lambda: models.User(email="admin@test.local", role="Admin", status="Active")

    client = TestClient(app)

    # Mock LINE APIs
    main.admin_bot_api = MagicMock()
    main.tenant_bot_api = MagicMock()
    main.line_bot_api = MagicMock()

    # 1. Test LINE Bot keyword trigger for "จองห้องพัก"
    event = MockEvent(user_id="U_APPLICANT_123", text="จองห้องพัก")
    db = TestingSessionLocal()
    handle_tenant_message(event, db=db)
    db.close()

    # Verify bot replied to applicant
    assert main.tenant_bot_api.reply_message.called or main.tenant_bot_api.push_message.called

    # 2. Test GET /booking redirect
    resp_redirect = client.get("/booking?uid=U_APPLICANT_123&lang=th", follow_redirects=False)
    assert resp_redirect.status_code == 307
    location = resp_redirect.headers.get("location")
    assert "/booking/" in location

    # Extract booking UUID
    booking_uuid = location.split("/booking/")[1].split("?")[0]

    # 3. Test GET /booking/{uuid} renders form with agreement
    resp_form = client.get(f"/booking/{booking_uuid}?uid=U_APPLICANT_123&lang=th")
    assert resp_form.status_code == 200
    assert "แบบฟอร์มการจองห้องพัก" in resp_form.text or "สัญญาและข้อตกลง" in resp_form.text

    # 4. Test POST without agreement acceptance (Must fail 400)
    invalid_payload = {
        "full_name": "สมชาย ใจดี",
        "phone_number": "0812345678",
        "workplace_name": "บริษัท ทดสอบ จำกัด",
        "job_position": "พนักงานทั่วไป",
        "workplace_phone": "021234567",
        "requested_move_in_date": "2026-09-01",
        "agreement_accepted": False
    }
    resp_no_agree = client.post(f"/booking/{booking_uuid}", json=invalid_payload)
    assert resp_no_agree.status_code == 400

    # 5. Test POST with missing fields (Must fail 400)
    missing_payload = {
        "full_name": "สมชาย ใจดี",
        "phone_number": "",
        "workplace_name": "บริษัท ทดสอบ จำกัด",
        "job_position": "พนักงานทั่วไป",
        "workplace_phone": "021234567",
        "requested_move_in_date": "2026-09-01",
        "agreement_accepted": True
    }
    resp_missing = client.post(f"/booking/{booking_uuid}", json=missing_payload)
    assert resp_missing.status_code == 400

    # 6. Test valid booking submission
    valid_payload = {
        "full_name": "สมชาย ใจดี",
        "phone_number": "0812345678",
        "workplace_name": "บริษัท สุขสวัสดิ์ วิศวกรรม จำกัด",
        "job_position": "วิศวกรระบบ",
        "workplace_phone": "028765432",
        "requested_move_in_date": "2026-09-01",
        "needs_bed": True,
        "needs_mattress": False,
        "line_user_id": "U_APPLICANT_123",
        "agreement_accepted": True,
        "language": "th"
    }
    resp_submit = client.post(f"/booking/{booking_uuid}", json=valid_payload)
    assert resp_submit.status_code == 200
    res_data = resp_submit.json()
    assert res_data["status"] == "Success"
    booking_id = res_data["booking_id"]

    # Verify saved in DB
    db = TestingSessionLocal()
    saved_booking = db.query(models.BookingRequest).filter(models.BookingRequest.id == booking_id).first()
    assert saved_booking is not None
    assert saved_booking.full_name == "สมชาย ใจดี"
    assert saved_booking.workplace_name == "บริษัท สุขสวัสดิ์ วิศวกรรม จำกัด"
    assert saved_booking.job_position == "วิศวกรระบบ"
    assert saved_booking.needs_bed == 1
    assert saved_booking.needs_mattress == 0
    assert saved_booking.status == "Pending"
    assert saved_booking.agreement_accepted == 1
    db.close()

    # 7. Test Admin listing bookings
    resp_list = client.get("/admin/bookings/list")
    assert resp_list.status_code == 200
    bookings_list = resp_list.json()
    assert len(bookings_list) >= 1
    target_b = next((b for b in bookings_list if b["full_name"] == "สมชาย ใจดี"), None)
    assert target_b is not None
    assert target_b["needs_bed"] is True
    assert target_b["needs_mattress"] is False

    # 8. Test Admin Approving Candidate
    db = TestingSessionLocal()
    room = db.query(models.Room).first()
    db.close()

    approve_payload = {
        "assigned_room_id": room.id,
        "admin_notes": "ผ่านเกณฑ์การพิจารณา เอกสารการทำงานชัดเจน"
    }
    resp_approve = client.post(f"/admin/booking/{booking_id}/approve", json=approve_payload)
    assert resp_approve.status_code == 200
    assert resp_approve.json()["status"] == "Success"

    # Verify status in DB
    db = TestingSessionLocal()
    approved_booking = db.query(models.BookingRequest).filter(models.BookingRequest.id == booking_id).first()
    assert approved_booking.status == "Approved"
    assert approved_booking.assigned_room_id == room.id
    assert approved_booking.needs_bed == 1
    assert approved_booking.needs_mattress == 0
    approved_tenant = db.query(models.Tenant).filter(
        models.Tenant.line_user_id == "U_APPLICANT_123",
        models.Tenant.status != "Active"
    ).order_by(models.Tenant.id.desc()).first()
    assert approved_tenant is not None
    assert approved_tenant.full_name == approved_booking.full_name
    assert approved_tenant.phone_number == approved_booking.phone_number
    assert approved_tenant.temp_building_id == room.building_id
    assert approved_tenant.current_room_id is None
    assert room.status == "Vacant"
    db.close()

    # Verify LINE notification was pushed
    assert main.tenant_bot_api.push_message.called

    # Approved applicants must see their allocated-room onboarding status, not
    # the generic "no room" booking invitation.
    main.tenant_bot_api.reset_mock()
    event = MockEvent(user_id="U_APPLICANT_123", text="ดูค่าเช่า")
    db = TestingSessionLocal()
    handle_tenant_message(event, db=db)
    db.close()
    assert main.tenant_bot_api.reply_message.called or main.tenant_bot_api.push_message.called
    line_call = (
        main.tenant_bot_api.reply_message.call_args
        if main.tenant_bot_api.reply_message.called
        else main.tenant_bot_api.push_message.call_args
    )
    assert "ผ่านการคัดเลือกแล้ว" in str(line_call)
    assert "ยังไม่มีห้องพักในระบบ" not in str(line_call)
    assert "/register/" in str(line_call)

    # 9. Test Admin Rejecting Candidate
    # Create another booking for rejection test
    booking_uuid2 = str(uuid.uuid4())
    reject_booking_payload = {
        "full_name": "สมศรี มีสุข",
        "phone_number": "0898765432",
        "workplace_name": "หจก. ทดสอบ",
        "job_position": "ธุรการ",
        "workplace_phone": "029999999",
        "requested_move_in_date": "2026-09-05",
        "needs_bed": False,
        "needs_mattress": True,
        "line_user_id": "U_APPLICANT_456",
        "agreement_accepted": True,
        "language": "th"
    }
    resp_submit2 = client.post(f"/booking/{booking_uuid2}", json=reject_booking_payload)
    booking_id2 = resp_submit2.json()["booking_id"]

    resp_reject = client.post(f"/admin/booking/{booking_id2}/reject", json={"reason": "ห้องพักเต็มในงวดที่ระบุ"})
    assert resp_reject.status_code == 200
    assert resp_reject.json()["status"] == "Success"

    db = TestingSessionLocal()
    rejected_booking = db.query(models.BookingRequest).filter(models.BookingRequest.id == booking_id2).first()
    assert rejected_booking.status == "Rejected"
    assert rejected_booking.needs_bed == 0
    assert rejected_booking.needs_mattress == 1
    db.close()

    # 10. Test Admin Deleting Booking
    resp_del = client.post(f"/admin/booking/{booking_id2}/delete")
    assert resp_del.status_code == 200
    assert resp_del.json()["status"] == "Success"

    db = TestingSessionLocal()
    deleted_booking = db.query(models.BookingRequest).filter(models.BookingRequest.id == booking_id2).first()
    assert deleted_booking is None
    db.close()

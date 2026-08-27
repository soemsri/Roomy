import pytest
from datetime import datetime, timedelta
import models

def test_create_parcel_model(db_session):
    building = models.Building(name="อาคาร A")
    db_session.add(building)
    db_session.commit()

    room = models.Room(room_number="101", building_id=building.id)
    db_session.add(room)
    db_session.commit()

    tenant = models.Tenant(
        full_name="สมชาย ทดสอบ",
        line_user_id="U123456789",
        current_room_id=room.id,
        status="Active"
    )
    db_session.add(tenant)
    db_session.commit()

    user = models.User(
        email="staff@example.com",
        full_name="Staff Member",
        role="Clerk"
    )
    db_session.add(user)
    db_session.commit()

    parcel = models.Parcel(
        room_id=room.id,
        tenant_id=tenant.id,
        carrier="Flash Express",
        tracking_number="TH99887766",
        parcel_image_url="/uploads/parcel_test.jpg",
        status="pending",
        notes="วางไว้หน้าห้อง",
        created_by_user_id=user.id
    )
    db_session.add(parcel)
    db_session.commit()
    db_session.refresh(parcel)

    assert parcel.id is not None
    assert parcel.carrier == "Flash Express"
    assert parcel.tracking_number == "TH99887766"
    assert parcel.status == "pending"
    assert parcel.arrived_at is not None
    assert parcel.received_at is None
    assert parcel.room.room_number == "101"
    assert parcel.tenant.full_name == "สมชาย ทดสอบ"
    assert parcel.created_by_user.email == "staff@example.com"

def test_parcel_mark_received(db_session):
    building = models.Building(name="อาคาร A")
    db_session.add(building)
    db_session.commit()

    room = models.Room(room_number="102", building_id=building.id)
    db_session.add(room)
    db_session.commit()

    parcel = models.Parcel(
        room_id=room.id,
        carrier="Kerry Express",
        status="pending"
    )
    db_session.add(parcel)
    db_session.commit()

    # Mark as received
    now = datetime.now()
    parcel.status = "received"
    parcel.received_at = now
    parcel.received_by_name = "สมศรี ตัวแทน"
    parcel.proof_image_url = "/uploads/proof_test.jpg"
    db_session.commit()

    updated = db_session.query(models.Parcel).filter(models.Parcel.id == parcel.id).first()
    assert updated.status == "received"
    assert updated.received_at is not None
    assert updated.received_by_name == "สมศรี ตัวแทน"
    assert updated.proof_image_url == "/uploads/proof_test.jpg"

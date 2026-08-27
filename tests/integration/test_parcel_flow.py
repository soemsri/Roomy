import pytest
import io
from datetime import datetime, timedelta
from unittest.mock import MagicMock
import models

@pytest.fixture
def auth_headers(client, db_session):
    user = models.User(
        email="admin_parcel@test.local",
        full_name="Admin Parcel Tester",
        role="Admin",
        session_token="valid_parcel_token",
        status="Active"
    )
    db_session.add(user)
    db_session.commit()
    return {"Cookie": "admin_session=valid_parcel_token"}

@pytest.fixture
def sample_setup(db_session):
    building = models.Building(name="อาคาร A")
    db_session.add(building)
    db_session.commit()

    room101 = models.Room(room_number="101", building_id=building.id)
    room102 = models.Room(room_number="102", building_id=building.id)
    db_session.add_all([room101, room102])
    db_session.commit()

    tenant = models.Tenant(
        full_name="คุณสมชาย ใจดี",
        phone_number="0812345678",
        line_user_id="U_TENANT_101",
        current_room_id=room101.id,
        status="Active",
        language="th"
    )
    db_session.add(tenant)
    db_session.commit()

    return {
        "building": building,
        "room101": room101,
        "room102": room102,
        "tenant": tenant
    }

def test_admin_create_parcel_and_notify(client, db_session, auth_headers, sample_setup, monkeypatch):
    mock_push = MagicMock()
    monkeypatch.setattr("services.line_bot.send_parcel_arrived_flex", mock_push)

    room = sample_setup["room101"]
    tenant = sample_setup["tenant"]

    # 1. Create parcel without photo
    res = client.post(
        "/admin/parcels/create",
        headers=auth_headers,
        data={
            "room_id": room.id,
            "carrier": "Flash Express",
            "tracking_number": "TH12345678",
            "notes": "ฝากไว้ที่เคาน์เตอร์"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    parcel_id = data["parcel_id"]

    parcel = db_session.query(models.Parcel).filter(models.Parcel.id == parcel_id).first()
    assert parcel is not None
    assert parcel.room_id == room.id
    assert parcel.tenant_id == tenant.id
    assert parcel.carrier == "Flash Express"
    assert parcel.tracking_number == "TH12345678"
    assert parcel.status == "pending"

    # Activity log recorded
    log = db_session.query(models.ApplicationLog).filter(models.ApplicationLog.action == "Create Parcel").first()
    assert log is not None
    assert "Room 101" in log.target

    # Notification mock called
    mock_push.assert_called_once()

def test_admin_create_parcel_with_image_upload(client, db_session, auth_headers, sample_setup):
    room = sample_setup["room102"]

    file_data = io.BytesIO(b"fake image data")
    res = client.post(
        "/admin/parcels/create",
        headers=auth_headers,
        data={
            "room_id": room.id,
            "carrier": "SPX Express",
            "tracking_number": "SPX987654"
        },
        files={"parcel_image": ("box.jpg", file_data, "image/jpeg")}
    )
    assert res.status_code == 200
    parcel_id = res.json()["parcel_id"]

    parcel = db_session.query(models.Parcel).filter(models.Parcel.id == parcel_id).first()
    assert parcel.parcel_image_url is not None
    assert parcel.parcel_image_url.startswith("/uploads/parcel_")

def test_admin_receive_parcel_one_click(client, db_session, auth_headers, sample_setup):
    room = sample_setup["room101"]
    tenant = sample_setup["tenant"]

    parcel = models.Parcel(
        room_id=room.id,
        tenant_id=tenant.id,
        carrier="Kerry Express",
        tracking_number="KER123",
        status="pending"
    )
    db_session.add(parcel)
    db_session.commit()

    # 1-Click Pickup (no proof image or custom receiver name provided)
    res = client.post(
        f"/admin/parcels/{parcel.id}/receive",
        headers=auth_headers,
        data={}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    db_session.refresh(parcel)
    assert parcel.status == "received"
    assert parcel.received_at is not None
    assert parcel.received_by_name == "คุณสมชาย ใจดี" # Defaults to tenant full name

    log = db_session.query(models.ApplicationLog).filter(models.ApplicationLog.action == "Receive Parcel").first()
    assert log is not None

def test_admin_receive_parcel_with_proof_and_name(client, db_session, auth_headers, sample_setup):
    room = sample_setup["room101"]

    parcel = models.Parcel(
        room_id=room.id,
        carrier="ไปรษณีย์ไทย",
        status="pending"
    )
    db_session.add(parcel)
    db_session.commit()

    file_data = io.BytesIO(b"signature proof")
    res = client.post(
        f"/admin/parcels/{parcel.id}/receive",
        headers=auth_headers,
        data={"received_by_name": "สมหญิง ภรรยา"},
        files={"proof_image": ("proof.jpg", file_data, "image/jpeg")}
    )
    assert res.status_code == 200

    db_session.refresh(parcel)
    assert parcel.status == "received"
    assert parcel.received_by_name == "สมหญิง ภรรยา"
    assert parcel.proof_image_url is not None
    assert parcel.proof_image_url.startswith("/uploads/parcel_proof_")

def test_admin_parcel_stats_and_listing(client, db_session, auth_headers, sample_setup):
    room1 = sample_setup["room101"]
    room2 = sample_setup["room102"]

    # Add 1 pending fresh
    p1 = models.Parcel(room_id=room1.id, carrier="Flash", status="pending", arrived_at=datetime.now())
    # Add 1 pending overdue (> 7 days)
    eight_days_ago = datetime.now() - timedelta(days=8)
    p2 = models.Parcel(room_id=room1.id, carrier="Kerry", status="pending", arrived_at=eight_days_ago)
    # Add 1 received today
    p3 = models.Parcel(room_id=room2.id, carrier="SPX", status="received", arrived_at=datetime.now(), received_at=datetime.now())
    db_session.add_all([p1, p2, p3])
    db_session.commit()

    # Test Stats API
    res = client.get("/admin/parcels/stats", headers=auth_headers)
    assert res.status_code == 200
    stats = res.json()
    assert stats["pending"] == 2
    assert stats["overdue"] == 1
    assert stats["received_today"] == 1
    assert stats["total_received"] == 1

    # Test List API with filters
    # Filter pending
    res = client.get("/admin/parcels/list?status=pending", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2

    # Filter overdue
    res = client.get("/admin/parcels/list?status=overdue", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["carrier"] == "Kerry"
    assert data["items"][0]["is_overdue"] is True

    # Search query
    res = client.get("/admin/parcels/list?q=SPX", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["carrier"] == "SPX"

def test_admin_delete_parcel(client, db_session, auth_headers, sample_setup):
    room = sample_setup["room101"]
    parcel = models.Parcel(room_id=room.id, carrier="Flash", status="pending")
    db_session.add(parcel)
    db_session.commit()
    pid = parcel.id

    res = client.post(f"/admin/parcels/{pid}/delete", headers=auth_headers)
    assert res.status_code == 200

    deleted = db_session.query(models.Parcel).filter(models.Parcel.id == pid).first()
    assert deleted is None

def test_tenant_view_parcels(client, db_session, sample_setup):
    tenant = sample_setup["tenant"]
    room = sample_setup["room101"]

    p1 = models.Parcel(room_id=room.id, carrier="Flash Express", tracking_number="TH111", status="pending")
    p2 = models.Parcel(room_id=room.id, carrier="Kerry", tracking_number="KER222", status="received", received_at=datetime.now(), received_by_name="สมชาย")
    db_session.add_all([p1, p2])
    db_session.commit()

    # Web View HTML
    res = client.get(f"/parcels/{tenant.uuid}")
    assert res.status_code == 200
    assert "Flash Express" in res.text
    assert "TH111" in res.text
    assert "พัสดุของฉัน" in res.text

    # JSON API
    res_api = client.get(f"/api/tenant/{tenant.uuid}/parcels")
    assert res_api.status_code == 200
    data = res_api.json()
    assert len(data["pending"]) == 1
    assert data["pending"][0]["carrier"] == "Flash Express"
    assert len(data["received"]) == 1
    assert data["received"][0]["tracking_number"] == "KER222"

def test_tenant_bot_parcel_keyword(client, db_session, sample_setup, monkeypatch):
    tenant = sample_setup["tenant"]
    room = sample_setup["room101"]

    p = models.Parcel(room_id=room.id, carrier="Flash Express", status="pending")
    db_session.add(p)
    db_session.commit()

    from controllers.callback import handle_tenant_message
    from types import SimpleNamespace

    event = SimpleNamespace(
        reply_token="test_reply_token",
        source=SimpleNamespace(user_id=tenant.line_user_id),
        message=SimpleNamespace(text="พัสดุ")
    )

    mock_push = MagicMock()
    monkeypatch.setattr("controllers.callback.safe_reply_or_push", mock_push)

    handle_tenant_message(event, db=db_session)
    mock_push.assert_called_once()
    args, _ = mock_push.call_args
    # Message should contain link to parcels
    messages = args[3]
    assert len(messages) > 0
    assert "parcels" in messages[0].text

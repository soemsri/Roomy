from datetime import datetime, timedelta
from unittest.mock import MagicMock

import models
import security


def create_reset_token(db_session, token="valid-reset-token", minutes=5, used=0):
    reset_token = models.PasswordResetToken(
        token=token,
        expires_at=datetime.now() + timedelta(minutes=minutes),
        used=used,
    )
    db_session.add(reset_token)
    db_session.commit()
    return reset_token


def test_password_reset_link_points_directly_to_reset_page(client, db_session, monkeypatch):
    import controllers.admin as admin_controller

    line_api = MagicMock()
    monkeypatch.setattr(admin_controller, "admin_bot_api", line_api)

    response = client.post("/admin/forgot-password")

    assert response.status_code == 200
    request = line_api.push_message.call_args.args[0]
    message = request.messages[0].text
    assert "/admin/reset-password?token=" in message
    assert "/admin/magic-login" not in message


def test_reset_page_requires_a_valid_token(client, db_session):
    expired = create_reset_token(db_session, token="expired-token", minutes=-1)

    assert client.get("/admin/reset-password").status_code == 400
    assert client.get("/admin/reset-password?token=unknown-token").status_code == 400
    assert client.get(f"/admin/reset-password?token={expired.token}").status_code == 400


def test_link_preview_does_not_consume_reset_token(client, db_session):
    reset_token = create_reset_token(db_session)

    first_preview = client.get(f"/admin/reset-password?token={reset_token.token}")
    second_preview = client.get(f"/admin/reset-password?token={reset_token.token}")

    assert first_preview.status_code == 200
    assert second_preview.status_code == 200
    db_session.refresh(reset_token)
    assert reset_token.used == 0


def test_reset_submission_requires_token_and_does_not_change_password(client, db_session):
    owner = db_session.query(models.Owner).first()
    original_hash = owner.password_hash

    response = client.post("/admin/reset-password", data={"new_password": "new-secure-password"})

    assert response.status_code == 422
    db_session.refresh(owner)
    assert owner.password_hash == original_hash


def test_reset_token_is_consumed_once(client, db_session):
    reset_token = create_reset_token(db_session)

    response = client.post(
        "/admin/reset-password",
        data={"token": reset_token.token, "new_password": "new-secure-password"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login?reset_success=1"
    db_session.refresh(reset_token)
    owner = db_session.query(models.Owner).first()
    assert reset_token.used == 1
    assert security.verify_password("new-secure-password", owner.password_hash)

    replay = client.post(
        "/admin/reset-password",
        data={"token": reset_token.token, "new_password": "attacker-password"},
        follow_redirects=False,
    )

    assert replay.status_code == 400
    db_session.refresh(owner)
    assert security.verify_password("new-secure-password", owner.password_hash)
    assert not security.verify_password("attacker-password", owner.password_hash)

from datetime import datetime, timedelta

import models


def create_magic_token(db_session, token="valid-magic-token"):
    owner = db_session.query(models.Owner).first()
    owner.magic_token = token
    owner.magic_token_expires = datetime.now() + timedelta(minutes=5)
    db_session.commit()
    return owner


def test_link_preview_does_not_consume_magic_token(client, db_session):
    owner = create_magic_token(db_session)

    first_preview = client.get(f"/admin/magic-login?token={owner.magic_token}")
    second_preview = client.get(f"/admin/magic-login?token={owner.magic_token}")

    assert first_preview.status_code == 200
    assert second_preview.status_code == 200
    assert 'action="/admin/magic-login"' in first_preview.text
    db_session.refresh(owner)
    assert owner.magic_token == "valid-magic-token"


def test_magic_login_post_consumes_token_and_preserves_section(client, db_session):
    owner = create_magic_token(db_session)

    response = client.post(
        "/admin/magic-login",
        data={"token": owner.magic_token, "fragment": "#leaseSection"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/dashboard#leaseSection"
    assert "admin_session=" in response.headers["set-cookie"]
    db_session.refresh(owner)
    assert owner.magic_token is None
    assert owner.magic_token_expires is None

    replay = client.post(
        "/admin/magic-login",
        data={"token": "valid-magic-token", "fragment": "#leaseSection"},
        follow_redirects=False,
    )
    assert replay.status_code == 400


def test_magic_login_allows_only_known_dashboard_targets(client, db_session):
    owner = create_magic_token(db_session)

    response = client.post(
        "/admin/magic-login",
        data={
            "token": owner.magic_token,
            "mode": "unexpected",
            "fragment": "#malicious",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/dashboard"

"""Tests for authentication flows: login, logout, RBAC, password change."""
from conftest import make_user, login

from app import db


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_valid_credentials_redirects(client, app):
    user = make_user(email="valid@test.example")
    db.session.commit()
    resp = login(client, user)
    assert resp.status_code == 302
    assert resp.location in ("/", "http://localhost/")


def test_login_invalid_password_shows_error(client, app):
    user = make_user(email="bad@test.example")
    db.session.commit()
    resp = client.post(
        "/auth/login",
        data={"email": user.email, "password": "totallyWrong!"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid email or password" in resp.data


def test_login_unknown_email_shows_error(client, app):
    resp = client.post(
        "/auth/login",
        data={"email": "nobody@test.example", "password": "password123"},
        follow_redirects=True,
    )
    assert b"Invalid email or password" in resp.data


def test_login_inactive_user_shows_deactivated(client, app):
    user = make_user(email="inactive@test.example", active=False)
    db.session.commit()
    resp = client.post(
        "/auth/login",
        data={"email": user.email, "password": "password123"},
        follow_redirects=True,
    )
    assert b"deactivated" in resp.data


def test_login_must_change_password_redirects_to_change(client, app):
    user = make_user(email="mustchange@test.example", must_change=True)
    db.session.commit()
    resp = login(client, user)
    assert resp.status_code == 302
    assert "/auth/change-password" in resp.location


def test_login_open_redirect_blocked(client, app):
    user = make_user(email="redirect@test.example")
    db.session.commit()
    resp = client.post(
        "/auth/login?next=https://evil.example",
        data={"email": user.email, "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "evil.example" not in resp.location


def test_login_relative_next_allowed(client, app):
    user = make_user(email="relnext@test.example")
    db.session.commit()
    resp = client.post(
        "/auth/login?next=/stats/",
        data={"email": user.email, "password": "password123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/stats/" in resp.location


def test_already_authenticated_redirects_from_login(client, app):
    user = make_user(email="authed@test.example")
    db.session.commit()
    login(client, user)
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.location in ("/", "http://localhost/")


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def test_logout_requires_post(client, app):
    user = make_user()
    db.session.commit()
    login(client, user)
    resp = client.get("/auth/logout")
    assert resp.status_code == 405


def test_logout_clears_session_and_redirects_to_login(client, app):
    user = make_user()
    db.session.commit()
    login(client, user)
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.location


def test_logout_unauthenticated_redirects_to_login(client):
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.location


# ---------------------------------------------------------------------------
# Register (disabled)
# ---------------------------------------------------------------------------

def test_register_disabled_redirects(client):
    resp = client.get("/auth/register", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Contact an administrator" in resp.data


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------

def test_change_password_success(client, app):
    user = make_user(email="chpw@test.example", password="oldpass123", must_change=True)
    db.session.commit()
    login(client, user, "oldpass123")

    resp = client.post(
        "/auth/change-password",
        data={"new_password": "newpass456!", "confirm_password": "newpass456!"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.expire_all()
    from app.models import User
    updated = db.session.get(User, user.id)
    assert updated.check_password("newpass456!")
    assert not updated.must_change_password


def test_change_password_too_short(client, app):
    user = make_user(email="short@test.example")
    db.session.commit()
    login(client, user)
    resp = client.post(
        "/auth/change-password",
        data={"new_password": "abc", "confirm_password": "abc"},
        follow_redirects=True,
    )
    assert b"8 characters" in resp.data


def test_change_password_mismatch(client, app):
    user = make_user(email="mismatch@test.example")
    db.session.commit()
    login(client, user)
    resp = client.post(
        "/auth/change-password",
        data={"new_password": "password123", "confirm_password": "different456"},
        follow_redirects=True,
    )
    assert b"do not match" in resp.data


def test_change_password_requires_login(client):
    resp = client.get("/auth/change-password", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.location


# ---------------------------------------------------------------------------
# Password reset token replay prevention
# ---------------------------------------------------------------------------

def test_reset_token_replay_blocked(client, app):
    from itsdangerous import URLSafeTimedSerializer

    user = make_user(email="replay@test.example", password="original123")
    db.session.commit()

    # Generate a valid token
    s = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="password-reset")
    token = s.dumps({"id": user.id, "ph": user.password_hash[-8:]})

    # First use: changes the password
    client.post(
        f"/auth/reset-password/{token}",
        data={"new_password": "newpass789!", "confirm_password": "newpass789!"},
        follow_redirects=False,
    )

    # Second use: token should now be rejected because password hash changed
    resp = client.post(
        f"/auth/reset-password/{token}",
        data={"new_password": "anotherpass!", "confirm_password": "anotherpass!"},
        follow_redirects=True,
    )
    assert b"already been used" in resp.data

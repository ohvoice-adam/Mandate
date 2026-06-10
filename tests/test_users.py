"""Tests for the user management routes (app/routes/users.py)."""
from app import db
from tests.conftest import make_user, login


def _edit_form_data(user, **overrides):
    """Base form payload for POST /users/<id>/edit, matching the edit template."""
    data = {
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "role": user.role,
        "organization_id": "",
        "is_active": "on",
    }
    data.update(overrides)
    return data


def test_edit_user_saves_phone(client):
    admin = make_user(role="admin")
    target = make_user(role="enterer")
    db.session.commit()
    login(client, admin)

    resp = client.post(
        f"/users/{target.id}/edit",
        data=_edit_form_data(target, phone="614-555-0123"),
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert resp.request.path == "/users/"
    assert b"updated successfully" in resp.data
    db.session.refresh(target)
    assert target.phone == "614-555-0123"


def test_edit_user_blank_phone_stored_as_null(client):
    admin = make_user(role="admin")
    target = make_user(role="enterer")
    target.phone = "614-555-0123"
    db.session.commit()
    login(client, admin)

    resp = client.post(
        f"/users/{target.id}/edit",
        data=_edit_form_data(target, phone="   "),
        follow_redirects=True,
    )

    assert resp.status_code == 200
    db.session.refresh(target)
    assert target.phone is None


def test_new_user_saves_phone(client):
    admin = make_user(role="admin")
    db.session.commit()
    login(client, admin)

    resp = client.post(
        "/users/new",
        data={
            "email": "newuser@test.example",
            "first_name": "New",
            "last_name": "Person",
            "phone": "614-555-0177",
            "role": "enterer",
            "organization_id": "",
        },
    )

    assert resp.status_code == 200
    assert b"invite" in resp.data.lower()
    from app.models import User
    created = User.query.filter_by(email="newuser@test.example").first()
    assert created is not None
    assert created.phone == "614-555-0177"


def test_new_user_blank_phone_stored_as_null(client):
    admin = make_user(role="admin")
    db.session.commit()
    login(client, admin)

    resp = client.post(
        "/users/new",
        data={
            "email": "nophone@test.example",
            "first_name": "No",
            "last_name": "Phone",
            "phone": "  ",
            "role": "enterer",
            "organization_id": "",
        },
    )

    assert resp.status_code == 200
    from app.models import User
    created = User.query.filter_by(email="nophone@test.example").first()
    assert created is not None
    assert created.phone is None


def test_index_displays_phone(client):
    admin = make_user(role="admin")
    target = make_user(role="enterer")
    target.phone = "614-555-0142"
    db.session.commit()
    login(client, admin)

    resp = client.get("/users/")

    assert resp.status_code == 200
    assert b"Phone" in resp.data
    assert b"614-555-0142" in resp.data


def test_edit_form_displays_existing_phone(client):
    admin = make_user(role="admin")
    target = make_user(role="enterer")
    target.phone = "614-555-0199"
    db.session.commit()
    login(client, admin)

    resp = client.get(f"/users/{target.id}/edit")

    assert resp.status_code == 200
    assert b'name="phone"' in resp.data
    assert b"614-555-0199" in resp.data

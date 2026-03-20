"""Unit tests for model methods — no HTTP, pure DB logic."""
from conftest import make_user, make_collector, make_voter

from app import db
from app.models import Settings


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

def test_user_check_password_correct(app):
    user = make_user(password="correcthorse99")
    assert user.check_password("correcthorse99")


def test_user_check_password_wrong(app):
    user = make_user(password="correcthorse99")
    assert not user.check_password("wrongpassword")


def test_user_is_admin_property(app):
    admin = make_user(role="admin")
    enterer = make_user(role="enterer")
    assert admin.is_admin
    assert not enterer.is_admin


def test_user_is_organizer_property(app):
    org = make_user(role="organizer")
    enterer = make_user(role="enterer")
    assert org.is_organizer
    assert not enterer.is_organizer
    assert org.is_admin_or_organizer
    assert not enterer.is_admin_or_organizer


def test_user_full_name(app):
    user = make_user(first_name="Alice", last_name="Smith")
    assert user.full_name == "Alice Smith"


def test_user_inactive_flag(app):
    user = make_user(active=False)
    assert not user.is_active


def test_user_set_password_updates_hash(app):
    user = make_user(password="oldpass123")
    old_hash = user.password_hash
    user.set_password("newpass456")
    assert user.password_hash != old_hash
    assert user.check_password("newpass456")
    assert not user.check_password("oldpass123")


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------

def test_settings_get_default_when_missing(app):
    result = Settings.get("nonexistent_key_xyz_abc", "fallback")
    assert result == "fallback"


def test_settings_get_returns_none_default(app):
    assert Settings.get("nonexistent_key_xyz_abc") is None


def test_settings_set_and_get(app):
    Settings.set("test_key_alpha", "hello_world")
    db.session.commit()
    assert Settings.get("test_key_alpha") == "hello_world"


def test_settings_overwrite(app):
    Settings.set("overwrite_key", "first")
    db.session.commit()
    Settings.set("overwrite_key", "second")
    db.session.commit()
    assert Settings.get("overwrite_key") == "second"


def test_settings_get_signature_goal_default(app):
    goal = Settings.get_signature_goal()
    assert goal == 0


def test_settings_set_signature_goal(app):
    Settings.set_signature_goal(5000)
    db.session.commit()
    assert Settings.get_signature_goal() == 5000


def test_settings_get_target_city_default(app):
    city = Settings.get_target_city()
    assert isinstance(city, str)
    assert city  # non-empty default

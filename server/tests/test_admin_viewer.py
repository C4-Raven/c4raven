"""Read-only admins (role admin_viewer).

They see the admin areas a superadmin switched on for them, can't change
anything, and never see an IP address. See raven/admin_view.py.
"""

import json
import time

import pytest
from flask import Flask
from flask_security import Security, SQLAlchemyUserDatastore, hash_password
from flask_security.models import fsqla_v3 as fsqla

from raven.admin_view import ADMIN_AREAS, admin_view, redact, redact_ips_for_viewer, redact_text
from raven.defaultconfig import DefaultConfig
from raven.extensions import babel, db

try:
    fsqla.FsModels.set_db_info(db)
except Exception:
    pass

from raven.blueprints.raven_api.group_api import group_api  # noqa: E402
from raven.blueprints.raven_api.user_api import user_api_blueprint  # noqa: E402


@pytest.fixture
def app(tmp_path):
    app = Flask("raven-test")
    app.config.from_object(DefaultConfig)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        RAVEN_DATA_FOLDER=str(tmp_path),
        RAVEN_ENABLE_LDAP=False,
        RAVEN_PROTECTED_USERNAMES=[],
        SECURITY_TWO_FACTOR=False,
        SECURITY_CSRF_PROTECT_MECHANISMS=[],
        WTF_CSRF_ENABLED=False,
        SECURITY_PASSWORD_HASH="plaintext",
        SECURITY_USER_IDENTITY_ATTRIBUTES=[
            {"username": {"mapper": lambda x: x, "case_insensitive": True}}
        ],
    )
    db.init_app(app)
    babel.init_app(app)

    from raven.models.EUD import EUD  # noqa: F401
    from raven.models.role import Role
    from raven.models.user import User
    from raven.models.WebAuthn import WebAuthn

    app.security = Security(app, SQLAlchemyUserDatastore(db, User, Role, WebAuthn))
    app.register_blueprint(user_api_blueprint)
    app.register_blueprint(group_api)
    app.after_request(redact_ips_for_viewer)

    with app.app_context():
        db.create_all()
        ds = app.security.datastore
        for role in ("administrator", "user", "admin_viewer"):
            ds.find_or_create_role(role)
        ds.create_user(username="root", password=hash_password("x"), roles=["administrator"])
        ds.create_user(
            username="viewer",
            password=hash_password("x"),
            roles=["user", "admin_viewer"],
            admin_view_areas=json.dumps(["users"]),
        )
        alice = ds.create_user(username="alice", password=hash_password("x"), roles=["user"])
        alice.current_login_ip = "203.0.113.7"
        alice.last_login_ip = "2001:db8::7"
        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


def login(app, username):
    with app.app_context():
        uniquifier = app.security.datastore.find_user(username=username).fs_uniquifier
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()
    return client


def test_viewer_reads_an_area_that_is_switched_on(app):
    r = login(app, "viewer").get("/api/users")
    assert r.status_code == 200, r.json


def test_viewer_is_refused_an_area_that_is_off(app):
    assert login(app, "viewer").get("/api/users/visibility").status_code == 403


def test_viewer_never_writes(app):
    viewer = login(app, "viewer")
    assert viewer.post("/api/user/add", json={"username": "x", "password": "y"}).status_code == 403
    assert viewer.post("/api/user/delete", json={"username": "alice"}).status_code == 403
    assert viewer.post("/api/user/role", json={"username": "alice", "roles": ["administrator"]}).status_code == 403
    assert viewer.put("/api/user/admin_view", json={"username": "viewer", "areas": list(ADMIN_AREAS)}).status_code == 403


def test_plain_user_gets_no_admin_reads(app):
    assert login(app, "alice").get("/api/users").status_code == 403


def test_viewer_sees_no_ip_addresses(app):
    body = login(app, "viewer").get("/api/users", query_string={"username": "alice"}).get_data(as_text=True)
    assert "203.0.113.7" not in body
    assert "2001:db8::7" not in body
    assert "hidden" in body


def test_superadmin_still_sees_ip_addresses(app):
    body = login(app, "root").get("/api/users", query_string={"username": "alice"}).get_data(as_text=True)
    assert "203.0.113.7" in body


def test_superadmin_switches_areas_on_and_off(app):
    root = login(app, "root")
    r = root.put("/api/user/admin_view", json={"username": "viewer", "areas": ["users", "groups"]})
    assert r.status_code == 200, r.json
    assert r.json["areas"] == ["users", "groups"]
    assert login(app, "viewer").get("/api/users/visibility").status_code == 200

    root.put("/api/user/admin_view", json={"username": "viewer", "areas": []})
    assert login(app, "viewer").get("/api/users").status_code == 403


def test_areas_are_validated(app):
    root = login(app, "root")
    assert root.put("/api/user/admin_view", json={"username": "viewer", "areas": ["everything"]}).status_code == 400
    # Only read-only admins have areas
    assert root.put("/api/user/admin_view", json={"username": "alice", "areas": ["users"]}).status_code == 400


def test_me_reports_the_areas(app):
    with app.app_context():
        user = app.security.datastore.find_user(username="viewer")
        assert user.serialize()["admin_view_areas"] == ["users"]


def test_unknown_area_in_decorator_fails_loudly():
    with pytest.raises(ValueError):
        admin_view("nope")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("from 192.168.1.20:8089:ssl", "from hidden:8089:ssl"),
        ("tcp://10.0.0.5:8087", "tcp://hidden:8087"),
        ("[fe80::1]:8080", "[hidden]:8080"),
        ("::ffff:10.1.2.3", "hidden"),
        ("2001:db8::42%eth0 up", "hidden up"),
        ("12:34:56", "12:34:56"),
        ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
        ("2026-11-14T12:34:56.123Z", "2026-11-14T12:34:56.123Z"),
        ("1.2.3.4.5", "1.2.3.4.5"),
        ("999.1.1.1", "999.1.1.1"),
    ],
)
def test_redact_text(text, expected):
    assert redact_text(text) == expected


def test_version_fields_are_left_alone():
    assert redact({"plugin_version": "5.6.0.123", "ip": "1.2.3.4"}) == {
        "plugin_version": "5.6.0.123",
        "ip": "hidden",
    }


def test_secrets_are_blanked_for_viewers():
    from raven.admin_view import redact_secrets

    policy = {"entities": [{"config": {"tokens": [{"token": "abc", "expiration": 5}]}}], "name": "p"}
    out = redact_secrets(policy)
    assert out["entities"][0]["config"]["tokens"][0] == {"token": "hidden", "expiration": 5}
    assert out["name"] == "p"
    assert redact_secrets({"cert_password": "s3cret"}) == {"cert_password": "hidden"}


def test_new_viewer_sees_every_area_until_switched_off():
    from raven.admin_view import ADMIN_AREAS, parse_areas

    assert parse_areas(None) == list(ADMIN_AREAS)
    assert parse_areas("[]") == []
    assert parse_areas([]) == []

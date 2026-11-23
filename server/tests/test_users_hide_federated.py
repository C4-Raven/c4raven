"""The Users page does not list the Federation Hub's device accounts.

The bridge creates an inactive "fed-<callsign>" account for every federated
device so it can be put in groups (FederationHubDaemon._ensure_federated_user).
GET /api/users leaves those out unless asked with include_federated=true; a
lookup by username still finds one; /api/users/all (the Groups member picker)
keeps them.
"""

import time

import pytest
from flask import Flask
from flask_security import Security, SQLAlchemyUserDatastore, hash_password
from flask_security.models import fsqla_v3 as fsqla

from raven.defaultconfig import DefaultConfig
from raven.extensions import babel, db

try:
    fsqla.FsModels.set_db_info(db)
except Exception:
    pass

from raven.blueprints.raven_api.user_api import user_api_blueprint  # noqa: E402

PEER = "peer-server-id"


@pytest.fixture
def admin(tmp_path):
    app = Flask("raven-test")
    app.config.from_object(DefaultConfig)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        RAVEN_DATA_FOLDER=str(tmp_path),
        RAVEN_ENABLE_LDAP=False,
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

    from raven.models.EUD import EUD
    from raven.models.role import Role
    from raven.models.user import User
    from raven.models.WebAuthn import WebAuthn

    app.security = Security(app, SQLAlchemyUserDatastore(db, User, Role, WebAuthn))
    app.register_blueprint(user_api_blueprint)

    with app.app_context():
        db.create_all()
        ds = app.security.datastore
        ds.find_or_create_role("administrator")
        ds.find_or_create_role("user")
        ds.create_user(username="admin", password=hash_password("x"), roles=["administrator"])
        alice = ds.create_user(username="alice", password=hash_password("x"), roles=["user"])
        # What the bridge creates for a federated device: inactive, no role.
        fed = ds.create_user(username="fed-BLUE FIRE", password=hash_password("x"), active=False)
        db.session.commit()
        db.session.add(EUD(uid="local-1", callsign="ALICE", user_id=alice.id))
        db.session.add(EUD(uid="S-1-5-21-0000-1001", callsign="BLUE FIRE", federated_from=PEER, user_id=fed.id))
        db.session.commit()
        uniquifier = ds.find_user(username="admin").fs_uniquifier

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()
    yield client

    with app.app_context():
        db.session.remove()
        db.drop_all()


def usernames(r):
    assert r.status_code == 200, r.json
    body = r.json
    return sorted(u["username"] for u in (body["results"] if isinstance(body, dict) else body))


def test_users_page_leaves_federated_accounts_out(admin):
    assert usernames(admin.get("/api/users")) == ["admin", "alice"]
    assert admin.get("/api/users").json["total"] == 2


def test_include_federated_shows_them(admin):
    assert usernames(admin.get("/api/users", query_string={"include_federated": "true"})) == [
        "admin",
        "alice",
        "fed-BLUE FIRE",
    ]


def test_lookup_by_username_still_finds_one(admin):
    assert usernames(admin.get("/api/users", query_string={"username": "fed-BLUE FIRE"})) == ["fed-BLUE FIRE"]


def test_groups_member_picker_keeps_them(admin):
    # /api/users/all feeds the Groups page's "add member" list, which is the
    # one place an administrator has to be able to reach a federated account.
    assert usernames(admin.get("/api/users/all")) == ["admin", "alice", "fed-BLUE FIRE"]

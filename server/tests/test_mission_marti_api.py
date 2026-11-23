"""Data Sync mission routes (raven/blueprints/marti_api/mission_marti_api.py) that the other
mission tests don't reach: subscriptions, invitations, roles, contents, uploads, log entries,
password, delete and the auth fixes (read-only subscribers can't write, GET by name needs a token,
a group mismatch on subscribe is a 403).

Minimal sqlite Flask app as in tests/test_sec_web_marti.py. RabbitMQ is replaced by a recorder.
"""

import datetime
import hashlib
import importlib
import io
import json
import time
import zipfile
from xml.etree.ElementTree import fromstring

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask_security import Security, SQLAlchemyUserDatastore, hash_password
from flask_security.models import fsqla_v3 as fsqla

from raven.defaultconfig import DefaultConfig
from raven.extensions import babel, db

try:
    fsqla.FsModels.set_db_info(db)
except Exception:
    pass

from raven.audit import init_audit  # noqa: E402

mission_module = importlib.import_module("raven.blueprints.marti_api.mission_marti_api")
from raven.models.CoT import CoT  # noqa: E402
from raven.models.EUD import EUD  # noqa: E402
from raven.models.Group import Group  # noqa: E402
from raven.models.GroupMission import GroupMission  # noqa: E402
from raven.models.Mission import Mission  # noqa: E402
from raven.models.MissionChange import MissionChange  # noqa: E402
from raven.models.MissionContent import MissionContent  # noqa: E402
from raven.models.MissionContentMission import MissionContentMission  # noqa: E402
from raven.models.MissionInvitation import MissionInvitation  # noqa: E402
from raven.models.MissionLogEntry import MissionLogEntry  # noqa: E402
from raven.models.MissionRole import MissionRole  # noqa: E402
from raven.models.MissionUID import MissionUID  # noqa: E402

# Long enough that the test-only plaintext hash is not mistaken for a des_crypt config string
PASSWORD = "mission-password-1"
OWNER, SUB, READER, OUTSIDER = "ANDROID-alice", "ANDROID-bob", "ANDROID-carol", "ANDROID-dave"


class _FakeCert:
    def __init__(self, cn):
        self._cn = cn

    def get_subject(self):
        return type("Subject", (), {"commonName": self._cn})()


class _Channel:
    def __init__(self, log):
        self.log = log

    def basic_publish(self, exchange, routing_key, body):
        self.log.append(("publish", exchange, routing_key, json.loads(body)))

    def queue_declare(self, queue):
        self.log.append(("declare", queue))

    def queue_bind(self, exchange, queue, routing_key):
        self.log.append(("bind", exchange, queue, routing_key))

    def queue_unbind(self, queue, exchange, routing_key):
        self.log.append(("unbind", exchange, queue, routing_key))


class _Connection:
    def __init__(self, log):
        self.log = log

    def channel(self):
        return _Channel(self.log)

    def close(self):
        pass


@pytest.fixture
def app(tmp_path, monkeypatch):
    ca = tmp_path / "ca"
    (ca / "certs" / "raven").mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (ca / "certs" / "raven" / "raven.nopass.key").write_bytes(private_pem)
    (ca / "certs" / "raven" / "raven.pub").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )

    app = Flask("raven-test")
    app.config.from_object(DefaultConfig)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        RAVEN_DATA_FOLDER=str(tmp_path),
        RAVEN_CA_FOLDER=str(ca),
        RAVEN_NODE_ID="node-1",
        RAVEN_ENABLE_LDAP=False,
        SECURITY_TWO_FACTOR=False,
        SECURITY_CSRF_PROTECT_MECHANISMS=[],
        WTF_CSRF_ENABLED=False,
        SECURITY_PASSWORD_HASH="plaintext",
        SECURITY_USER_IDENTITY_ATTRIBUTES=[
            {"username": {"mapper": lambda x: x, "case_insensitive": True}}
        ],
    )
    app.private_pem = private_pem
    db.init_app(app)
    babel.init_app(app)

    from raven.models.role import Role
    from raven.models.user import User
    from raven.models.WebAuthn import WebAuthn

    app.security = Security(app, SQLAlchemyUserDatastore(db, User, Role, WebAuthn))
    init_audit(app)
    app.register_blueprint(mission_module.mission_marti_api)

    log = []
    monkeypatch.setattr(mission_module, "_rabbit_connection", lambda: _Connection(log))
    app.rabbit = log

    with app.app_context():
        db.create_all()
        ds = app.security.datastore
        ds.find_or_create_role("administrator")
        ds.find_or_create_role("user")
        for name, role in (
            ("admin", "administrator"),
            ("alice", "user"),
            ("bob", "user"),
            ("carol", "user"),
            ("dave", "user"),
        ):
            ds.create_user(username=name, password=hash_password("x"), roles=[role])
        db.session.commit()
        for name in ("admin", "alice", "bob", "carol", "dave"):
            db.session.add(
                EUD(
                    uid=f"ANDROID-{name}",
                    callsign=name.upper(),
                    user_id=ds.find_user(username=name).id,
                )
            )
        db.session.add(
            Mission(
                name="op",
                guid="guid-op",
                creator_uid=OWNER,
                tool="public",
                default_role=MissionRole.MISSION_SUBSCRIBER,
                create_time=datetime.datetime(2026, 11, 1, 9, 0),
            )
        )
        db.session.commit()
        now = datetime.datetime.now(datetime.timezone.utc)
        for uid, username, role in (
            (OWNER, "alice", MissionRole.MISSION_OWNER),
            (SUB, "bob", MissionRole.MISSION_SUBSCRIBER),
            (READER, "carol", MissionRole.MISSION_READ_ONLY),
        ):
            db.session.add(
                MissionRole(
                    clientUid=uid,
                    username=username,
                    createTime=now,
                    role_type=role,
                    mission_name="op",
                )
            )
        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def as_cert(monkeypatch):
    def use(cn):
        fake = (lambda: _FakeCert(cn)) if cn else (lambda: False)
        monkeypatch.setattr(mission_module, "verify_client_cert", fake)

    return use


def token_for(app, uid, mission="op", guid="guid-op", **extra):
    claims = {"sub": uid, "MISSION_NAME": mission, "MISSION_GUID": guid, "iat": int(time.time())}
    claims.update(extra)
    return jwt.encode(claims, app.private_pem, algorithm="RS256")


def auth(app, uid, **kwargs):
    return {"Authorization": f"Bearer {token_for(app, uid, **kwargs)}"}


def published(app, exchange=None):
    return [e for e in app.rabbit if e[0] == "publish" and (exchange is None or e[1] == exchange)]


def query(app, model, **filters):
    with app.app_context():
        return db.session.query(model).filter_by(**filters).all()


# --- Tokens ------------------------------------------------------------------------------------


def test_token_lifetime(app):
    with app.app_context():
        for value, seconds in ((None, 30), ("", 30), ("abc", 30), (0.25, 1), (2.5, 2.5), (7, 7)):
            app.config["RAVEN_MISSION_TOKEN_DAYS"] = value
            assert mission_module.mission_token_lifetime() == int(seconds * 86400)


def test_token_claims_and_header_order(app):
    with app.test_request_context("https://tak.example:8443/x"):
        mission = Mission(name="op", guid="guid-op")
        token = mission_module.generate_token(mission, OWNER)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == "tak.example"
    assert claims["MISSION_NAME"] == "op" and claims["MISSION_GUID"] == "guid-op"
    assert claims["exp"] - claims["iat"] == 30 * 86400
    with app.test_request_context(
        "/", headers={"MissionAuthorization": "nope", "Authorization": f"Bearer {token}"}
    ):
        assert mission_module.verify_token()["sub"] == OWNER
    with app.test_request_context("/", headers={"Authorization": "Bearer garbage"}):
        assert mission_module.verify_token() is False


# --- Reading a mission -------------------------------------------------------------------------


def test_get_mission_by_name_needs_no_token(app):
    """S3 7.5: no token, cert or password check (clients read it before subscribing)"""
    client = app.test_client()
    r = client.get("/Marti/api/missions/op")
    assert r.status_code == 200
    assert r.json["type"] == "Mission" and r.json["version"] == "3" and r.json["nodeId"] == "node-1"
    assert r.json["data"][0]["name"] == "op"
    assert "logs" not in r.json["data"][0]
    r = client.get("/Marti/api/missions/op", query_string={"logs": "TRUE"})
    assert r.json["data"][0]["logs"] == []
    # A token for another mission doesn't matter either
    r = client.get("/Marti/api/missions/op", headers=auth(app, OWNER, mission="x"))
    assert r.status_code == 200
    # Password-protected missions are still described (passwordProtected tells the client to ask)
    with app.app_context():
        mission = db.session.get(Mission, "op")
        mission.password = hash_password(PASSWORD)
        mission.password_protected = True
        db.session.commit()
    r = client.get("/Marti/api/missions/op")
    assert r.status_code == 200 and r.json["data"][0]["passwordProtected"] is True
    assert client.get("/Marti/api/missions/nope").status_code == 404


def test_get_mission_by_guid_and_password(app):
    client = app.test_client()
    headers = auth(app, SUB)
    assert client.get("/Marti/api/missions/guid/guid-op").status_code == 401
    assert client.get("/Marti/api/missions/guid/guid-op", headers=headers).status_code == 200
    r = client.put(
        "/Marti/api/missions/op/password",
        query_string={"password": PASSWORD},
        headers=auth(app, OWNER),
    )
    assert r.status_code == 200 and r.json == {"success": True}
    assert client.get("/Marti/api/missions/guid/guid-op", headers=headers).status_code == 401
    r = client.get(
        "/Marti/api/missions/guid/guid-op", query_string={"password": PASSWORD}, headers=headers
    )
    assert r.status_code == 200 and r.json["data"][0]["passwordProtected"] is True
    other = auth(app, SUB, guid="nope")
    assert client.get("/Marti/api/missions/guid/nope", headers=other).status_code == 404


def test_password_needs_owner(app):
    client = app.test_client()
    r = client.put(
        "/Marti/api/missions/op/password",
        query_string={"password": PASSWORD},
        headers=auth(app, SUB),
    )
    assert r.status_code == 403
    assert (
        client.put("/Marti/api/missions/op/password", headers=auth(app, OWNER)).status_code == 400
    )
    assert (
        client.delete("/Marti/api/missions/op/password", headers=auth(app, OWNER)).status_code
        == 200
    )
    assert client.delete("/Marti/api/missions/op/password").status_code == 401


def test_list_missions_visibility(app, as_cert):
    with app.app_context():
        group = Group()
        group.name, group.type = "blue", "SYSTEM"
        db.session.add(group)
        db.session.add(Mission(name="secret", guid="g2", tool="public"))
        db.session.add(Mission(name="locked", guid="g3", tool="public", password_protected=True))
        db.session.add(Mission(name="other-tool", guid="g4", tool="citrap"))
        db.session.commit()
        db.session.add(GroupMission(group_id=group.id, mission_name="secret"))
        db.session.commit()

    as_cert(None)
    r = app.test_client().get("/Marti/api/missions")
    assert r.status_code == 401 and r.data == b""

    as_cert("bob")
    names = [m["name"] for m in app.test_client().get("/Marti/api/missions").json["data"]]
    assert names == ["op"]
    r = app.test_client().get("/Marti/api/missions", query_string={"passwordProtected": "TRUE"})
    assert sorted(m["name"] for m in r.json["data"]) == ["locked", "op"]
    r = app.test_client().get("/Marti/api/missions", query_string={"tool": "citrap"})
    assert [m["name"] for m in r.json["data"]] == ["other-tool"]

    as_cert("admin")
    names = [m["name"] for m in app.test_client().get("/Marti/api/missions").json["data"]]
    assert sorted(names) == ["op", "secret"]

    as_cert("nobody")
    assert app.test_client().get("/Marti/api/missions").status_code == 403


# --- Creating, changing and deleting -----------------------------------------------------------


def test_create_mission(app, as_cert):
    as_cert("bob")
    r = app.test_client().put(
        "/Marti/api/missions/new-op",
        query_string={"creatorUid": SUB, "description": "d", "password": PASSWORD},
    )
    assert r.status_code == 201, r.json
    data = r.json["data"][0]
    assert data["ownerRole"] == MissionRole.OWNER_ROLE
    assert data["passwordProtected"] is True
    assert jwt.decode(data["token"], options={"verify_signature": False})["sub"] == SUB
    roles = query(app, MissionRole, mission_name="new-op")
    assert [(r.clientUid, r.username, r.role_type) for r in roles] == [
        (SUB, "bob", MissionRole.MISSION_OWNER)
    ]
    assert [c.change_type for c in query(app, MissionChange, mission_name="new-op")] == [
        MissionChange.CREATE_MISSION
    ]

    # Missing creatorUid, unknown EUD
    assert app.test_client().put("/Marti/api/missions/x").status_code == 400
    r = app.test_client().put("/Marti/api/missions/x", query_string={"creatorUid": "nope"})
    assert r.status_code == 400


def test_create_mission_with_unknown_group_writes_nothing(app, as_cert):
    as_cert("bob")
    r = app.test_client().put(
        "/Marti/api/missions/new-op", query_string={"creatorUid": SUB, "group": "nope"}
    )
    assert r.status_code == 404
    assert query(app, Mission, name="new-op") == []


def test_create_mission_needs_a_cert(app, as_cert):
    as_cert(None)
    r = app.test_client().put("/Marti/api/missions/new-op", query_string={"creatorUid": SUB})
    assert r.status_code == 400


def test_delete_mission(app):
    client = app.test_client()
    assert client.delete("/Marti/api/missions/op", headers=auth(app, SUB)).status_code == 403
    assert client.delete("/Marti/api/missions/nope", headers=auth(app, OWNER)).status_code == 404
    assert client.delete("/Marti/api/missions", query_string={"guid": "nope"}).status_code == 404
    r = client.delete(
        "/Marti/api/missions", query_string={"guid": "guid-op"}, headers=auth(app, OWNER)
    )
    assert r.status_code == 200 and r.json["data"][0]["name"] == "op"
    assert query(app, Mission, name="op") == []
    (event,) = published(app, "missions")
    assert event[2] == "missions"
    assert fromstring(event[3]["cot"]).get("type") == "t-x-m-d"
    assert event[3]["uid"] == "node-1"


def test_token_without_mission_name_is_a_401_not_a_500(app):
    headers = {
        "Authorization": f"Bearer {jwt.encode({'sub': OWNER}, app.private_pem, algorithm='RS256')}"
    }
    assert app.test_client().delete("/Marti/api/missions/op", headers=headers).status_code == 401


# --- Subscriptions -----------------------------------------------------------------------------


def test_subscribe_by_uid(app, as_cert):
    with app.app_context():
        db.session.add(
            MissionInvitation(mission_name="op", client_uid=OUTSIDER, type="clientUid", role="x")
        )
        db.session.commit()
    as_cert("dave")
    client = app.test_client()
    assert client.put("/Marti/api/missions/op/subscription").status_code == 400
    r = client.put("/Marti/api/missions/op/subscription", query_string={"uid": SUB})
    assert r.status_code == 403
    r = client.put("/Marti/api/missions/op/subscription", query_string={"uid": OUTSIDER})
    assert r.status_code == 201, r.json
    assert r.json["type"] == "com.bbn.marti.sync.model.MissionSubscription"
    assert r.json["data"]["clientUid"] == OUTSIDER
    assert r.json["data"]["role"] == MissionRole.SUBSCRIBER_ROLE
    assert r.json["data"]["mission"]["name"] == "op"
    assert ("bind", "missions", OUTSIDER, "missions.op") in app.rabbit
    assert query(app, MissionInvitation, mission_name="op") == []

    r = client.get("/Marti/api/missions/op/subscription", query_string={"uid": OUTSIDER})
    assert r.status_code == 200 and r.json["data"]["clientUid"] == OUTSIDER
    r = client.get("/Marti/api/missions/op/subscription", query_string={"uid": SUB})
    assert r.status_code == 403
    assert client.get("/Marti/api/missions/op/subscription").status_code == 404

    r = client.delete("/Marti/api/missions/op/subscription", headers=auth(app, OUTSIDER))
    assert r.status_code == 200
    assert query(app, MissionRole, clientUid=OUTSIDER) == []
    assert ("unbind", "missions", OUTSIDER, "missions.op") in app.rabbit


def test_subscribe_password_and_token_path(app, as_cert):
    with app.app_context():
        mission = db.session.get(Mission, "op")
        mission.password = hash_password(PASSWORD)
        mission.password_protected = True
        db.session.commit()
    as_cert("dave")
    client = app.test_client()
    r = client.put("/Marti/api/missions/op/subscription", query_string={"uid": OUTSIDER})
    assert r.status_code == 401
    r = client.put(
        "/Marti/api/missions/guid/guid-op/subscription",
        query_string={"uid": OUTSIDER, "password": PASSWORD},
    )
    assert r.status_code == 201
    # A token for the mission subscribes without the password
    r = client.put("/Marti/api/missions/op/subscription", headers=auth(app, READER))
    assert r.status_code == 201
    assert r.json["data"]["role"] == MissionRole.READ_ONLY_ROLE
    assert "mission" not in r.json["data"]


def test_subscribe_group_mismatch_is_403(app, as_cert):
    with app.app_context():
        group = Group()
        group.name, group.type = "blue", "SYSTEM"
        db.session.add(group)
        db.session.commit()
        db.session.add(GroupMission(group_id=group.id, mission_name="op"))
        db.session.commit()
    as_cert("dave")
    r = app.test_client().put("/Marti/api/missions/op/subscription", query_string={"uid": OUTSIDER})
    assert r.status_code == 403
    assert "not in the same group" in r.json["error"]
    as_cert("admin")
    r = app.test_client().put(
        "/Marti/api/missions/op/subscription", query_string={"uid": "ANDROID-admin"}
    )
    assert r.status_code == 201


def test_roles_and_subscriptions(app):
    client = app.test_client()
    r = client.get("/Marti/api/missions/op/subscriptions/roles", headers=auth(app, READER))
    assert r.status_code == 200 and len(r.json["data"]) == 3
    r = client.get("/Marti/api/missions/guid/guid-op/subscriptions", headers=auth(app, READER))
    assert sorted(r.json["data"]) == sorted([OWNER, SUB, READER])
    r = client.get("/Marti/api/missions/op/role", headers=auth(app, READER))
    assert r.json["type"] == "com.bbn.marti.sync.model.MissionRole"
    assert r.json["data"] == MissionRole.READ_ONLY_ROLE
    r = client.get(
        "/Marti/api/missions/op/role", query_string={"clientUid": "x"}, headers=auth(app, READER)
    )
    assert r.json["data"] == MissionRole.SUBSCRIBER_ROLE


def test_owner_changes_and_removes_roles(app):
    client = app.test_client()
    url = "/Marti/api/missions/op/role"
    assert (
        client.put(url, query_string={"clientUid": READER}, headers=auth(app, SUB)).status_code
        == 403
    )
    owner = auth(app, OWNER)
    assert client.put(url, headers=owner).status_code == 400
    assert client.put(url, query_string={"clientUid": "nope"}, headers=owner).status_code == 400
    r = client.put(url, query_string={"clientUid": READER, "role": "BOSS"}, headers=owner)
    assert r.status_code == 400
    r = client.put(
        url,
        query_string={"clientUid": READER, "role": "MISSION_READONLY_SUBSCRIBER"},
        headers=owner,
    )
    assert r.status_code == 200
    (row,) = query(app, MissionRole, clientUid=READER)
    assert row.role_type == MissionRole.MISSION_READ_ONLY
    (event,) = published(app, "dms")
    assert event[2] == READER
    cot = fromstring(event[3]["cot"])
    assert cot.get("type") == "t-x-m-r"
    assert cot.find("detail/mission/role").get("type") == MissionRole.MISSION_READ_ONLY

    r = client.put(url, query_string={"clientUid": READER}, headers=owner)
    assert r.status_code == 200
    assert query(app, MissionRole, clientUid=READER) == []
    assert fromstring(published(app, "dms")[-1][3]["cot"]).find("detail/mission/role") is None


# --- Invitations -------------------------------------------------------------------------------


def test_invitations(app, as_cert):
    client = app.test_client()
    url = f"/Marti/api/missions/op/invite/clientUid/{OUTSIDER}"
    assert client.put(url).status_code == 401
    # Read-only subscribers can't invite
    assert client.put(url, headers=auth(app, READER)).status_code == 403
    r = client.put(url, query_string={"role": "MISSION_OWNER"}, headers=auth(app, SUB))
    assert r.status_code == 200 and r.data == b""
    (invite,) = query(app, MissionInvitation, mission_name="op")
    assert (invite.client_uid, invite.type, invite.role, invite.creator_uid) == (
        OUTSIDER,
        "clientUid",
        MissionRole.MISSION_OWNER,
        OWNER,
    )
    (event,) = published(app, "dms")
    assert event[2] == OUTSIDER
    assert fromstring(event[3]["cot"]).get("type") == "t-x-m-i"

    bad = "/Marti/api/missions/op/invite/clientUid/nope"
    assert client.put(bad, headers=auth(app, SUB)).status_code == 404
    bad = "/Marti/api/missions/op/invite/planet/x"
    assert client.put(bad, headers=auth(app, SUB)).status_code == 400

    r = client.post(
        "/Marti/api/missions/op/invite",
        json=[{"type": "callsign", "invitee": "CAROL", "role": {"type": "MISSION_SUBSCRIBER"}}],
        headers=auth(app, SUB),
    )
    assert r.status_code == 200 and r.json == {"success": True}
    r = client.post("/Marti/api/missions/op/invite", json={}, headers=auth(app, SUB))
    assert r.status_code == 400
    r = client.post("/Marti/api/missions/op/invite", json=[{"type": "x"}], headers=auth(app, SUB))
    assert r.status_code == 400

    as_cert("dave")
    r = client.get("/Marti/api/missions/invitations", query_string={"clientUid": OUTSIDER})
    assert (
        r.status_code == 200 and r.json["type"] == "MissionInvitation" and r.json["messages"] == []
    )
    (row,) = r.json["data"]
    assert row["invitee"] == OUTSIDER and row["missionGuid"] == "guid-op" and row["token"]
    r = client.get("/Marti/api/missions/all/invitations/", query_string={"clientUid": OUTSIDER})
    assert r.json["data"] == ["op"]
    r = client.get("/Marti/api/missions/invitations", query_string={"clientUid": SUB})
    assert r.status_code == 403
    r = client.get("/Marti/api/missions/guid/nope/invitations")
    assert r.status_code == 200 and r.json["data"] == []

    r = client.delete(url, headers=auth(app, SUB))
    assert r.status_code == 200
    assert [i.callsign for i in query(app, MissionInvitation, mission_name="op")] == ["CAROL"]
    r = client.delete("/Marti/api/missions/op/invite/planet/x", headers=auth(app, SUB))
    assert r.status_code == 400


# --- Keywords, log, changes --------------------------------------------------------------------


def test_keywords(app):
    client = app.test_client()
    url = "/Marti/api/missions/op/keywords"
    assert client.put(url, json=["a"], headers=auth(app, READER)).status_code == 403
    assert client.put(url, json=["a", "b"], headers=auth(app, SUB)).status_code == 200
    assert client.put(url, json=["b", "c"], headers=auth(app, SUB)).status_code == 200
    assert query(app, Mission, name="op")[0].keywords == ["a", "b", "c"]


def test_log_entries(app):
    client = app.test_client()
    url = "/Marti/api/missions/logs/entries"
    body = {
        "missionNames": ["op"],
        "content": "hello",
        "creatorUid": SUB,
        "dtg": "2026-11-01T10:00:00.000Z",
    }
    assert client.post(url, json=body, headers=auth(app, READER)).status_code == 403
    assert client.post(url, json={}, headers=auth(app, SUB)).status_code == 404
    r = client.post(url, json=body, headers=auth(app, SUB))
    assert r.status_code == 201
    assert r.json["type"] == "com.bbn.marti.sync.model.LogEntry"
    assert r.json["data"][0]["content"] == "hello"
    (event,) = published(app, "missions")
    assert event[2] == "missions.op" and event[3]["uid"] == SUB
    assert fromstring(event[3]["cot"]).get("type") == "t-x-m-c-l"
    r = client.get("/Marti/api/missions/op/log", headers=auth(app, READER))
    assert [e["content"] for e in r.json["data"]] == ["hello"]
    assert len(query(app, MissionLogEntry, mission_name="op")) == 1


# --- Content -----------------------------------------------------------------------------------


def test_upload_attach_and_detach_content(app, as_cert, tmp_path):
    as_cert("bob")
    client = app.test_client()
    data = b"hello world"
    file_hash = hashlib.sha256(data).hexdigest()
    r = client.post(
        "/Marti/sync/upload",
        query_string={"name": "notes.txt", "creatorUid": SUB, "keywords": ["k"]},
        data=data,
        content_type="text/plain",
    )
    assert r.status_code == 200, r.json
    assert r.json["Hash"] == file_hash and r.json["Name"] == "notes.txt"
    assert (tmp_path / "missions" / "notes.txt").read_bytes() == data
    r = client.post("/Marti/sync/upload", query_string={"name": "x.exe"}, data=b"x")
    assert r.status_code == 400
    assert client.post("/Marti/sync/upload", data=b"x").status_code == 400

    kw = f"/Marti/api/sync/metadata/{file_hash}/keywords"
    assert client.put(kw, json=["k", "j"]).status_code == 200
    assert query(app, MissionContent, hash=file_hash)[0].keywords == ["k", "j"]
    assert client.put("/Marti/api/sync/metadata/nope/keywords", json=[]).status_code == 400
    as_cert(None)
    assert client.put(kw, json=["z"]).status_code == 400

    url = "/Marti/api/missions/op/contents"
    assert (
        client.put(url, json={"hashes": [file_hash]}, headers=auth(app, READER)).status_code == 403
    )
    # A bad hash after a good one changes nothing
    r = client.put(url, json={"hashes": [file_hash, "nope"]}, headers=auth(app, SUB))
    assert r.status_code == 404
    assert query(app, MissionContentMission, mission_name="op") == []
    assert published(app) == []

    r = client.put(url, json={"hashes": [file_hash]}, headers=auth(app, SUB))
    assert r.status_code == 200 and r.json["type"] == "Mission"
    assert len(query(app, MissionContentMission, mission_name="op")) == 1
    (event,) = published(app, "missions")
    assert event[2] == "missions.op"
    # Re-adding doesn't announce again
    client.put(url, json={"hashes": [file_hash]}, headers=auth(app, SUB))
    assert len(published(app)) == 1

    r = client.delete(url, query_string={"hash": file_hash}, headers=auth(app, READER))
    assert r.status_code == 403
    r = client.delete(
        url, query_string={"hash": file_hash, "creatorUid": SUB}, headers=auth(app, SUB)
    )
    assert r.status_code == 200
    assert query(app, MissionContentMission, mission_name="op") == []
    assert query(app, MissionContent, hash=file_hash)
    assert [c.change_type for c in query(app, MissionChange, mission_name="op")] == [
        MissionChange.ADD_CONTENT,
        MissionChange.REMOVE_CONTENT,
    ]
    assert (
        client.delete(url, query_string={"hash": "nope"}, headers=auth(app, SUB)).status_code == 404
    )


def test_attach_and_detach_map_item(app):
    with app.app_context():
        now = datetime.datetime.now(datetime.timezone.utc)
        db.session.add(
            CoT(
                uid="marker-1",
                type="a-u-G",
                how="h-g-i-g-o",
                timestamp=now,
                start=now,
                stale=now,
                xml='<event uid="marker-1" type="a-u-G"><point lat="1.5" lon="2.5"/>'
                '<detail><contact callsign="M1"/><color argb="-1"/></detail></event>',
            )
        )
        db.session.commit()
    client = app.test_client()
    url = "/Marti/api/missions/op/contents"
    r = client.put(
        url, json={"uids": ["marker-1"]}, query_string={"creatorUid": SUB}, headers=auth(app, SUB)
    )
    assert r.status_code == 200, r.json
    (item,) = query(app, MissionUID, uid="marker-1")
    assert (item.mission_name, item.callsign, item.color, item.latitude) == ("op", "M1", "-1", 1.5)
    assert len(published(app, "missions")) == 1

    r = client.delete(url, query_string={"uid": "nope"}, headers=auth(app, SUB))
    assert r.status_code == 404
    r = client.delete(url, query_string={"uid": "marker-1"}, headers=auth(app, SUB))
    assert r.status_code == 200
    assert query(app, MissionUID, uid="marker-1")[0].mission_name is None
    cot = fromstring(published(app, "missions")[-1][3]["cot"])
    assert cot.find(".//details").get("callsign") == "M1"


def _package(items):
    buf = io.BytesIO()
    contents = "".join(f'<Content zipEntry="{uid}/{uid}.cot"/>' for uid in items)
    manifest = (
        '<MissionPackageManifest version="2"><Configuration><Parameter name="uid" value="pkg-1"/>'
        f"</Configuration><Contents>{contents}</Contents></MissionPackageManifest>"
    )
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("MANIFEST/manifest.xml", manifest)
        for uid in items:
            z.writestr(
                f"{uid}/{uid}.cot",
                f'<event version="2.0" uid="{uid}" type="a-f-G" how="h-e" time="2026-11-01T10:00:00Z" '
                f'start="2026-11-01T10:00:00Z" stale="2026-11-02T10:00:00Z"><point lat="3" lon="4"/>'
                f'<detail><contact callsign="P-{uid}"/></detail></event>',
            )
    return buf.getvalue()


def test_mission_package_upload(app, as_cert):
    as_cert("bob")
    client = app.test_client()
    url = "/Marti/api/missions/op/contents/missionpackage"
    assert (
        client.put("/Marti/api/missions/nope/contents/missionpackage", data=b"x").status_code == 404
    )
    r = client.put(url, query_string={"creatorUid": SUB}, data=_package(["p1", "p2"]))
    assert r.status_code == 200, r.json
    assert r.json["type"] == "MissionChange"
    (content,) = query(app, MissionContent, uid="pkg-1")
    assert content.filename.startswith("op_") and content.filename.endswith(".zip")
    assert sorted(u.uid for u in query(app, MissionUID, mission_name="op")) == ["p1", "p2"]
    assert sorted(c.uid for c in query(app, CoT, mission_name="op")) == ["p1", "p2"]
    assert len(published(app, "missions")) == 2
    assert all(c.content_uid == "pkg-1" for c in query(app, MissionChange, mission_name="op"))

    r = client.get("/Marti/api/missions/op/cot", headers=auth(app, READER))
    assert r.status_code == 200 and r.mimetype == "application/xml"
    assert sorted(e.get("uid") for e in fromstring(r.data)) == ["p1", "p2"]
    r = client.get("/Marti/api/missions/guid/nope/cot", headers=auth(app, READER, guid="nope"))
    assert fromstring(r.data).tag == "events" and len(fromstring(r.data)) == 0

    # A plain zip gets one ADD_CONTENT change
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.txt", "a")
    r = client.put(url, query_string={"creatorUid": SUB}, data=plain.getvalue())
    assert r.status_code == 200
    assert len(published(app, "missions")) == 3

    monkey = mission_module.MAX_MISSION_PACKAGE_ENTRY_BYTES
    try:
        mission_module.MAX_MISSION_PACKAGE_ENTRY_BYTES = 10
        assert client.put(url, data=_package(["p3"])).status_code == 413
    finally:
        mission_module.MAX_MISSION_PACKAGE_ENTRY_BYTES = monkey

    as_cert(None)
    assert client.put(url, data=b"x").status_code == 400


def test_changes_list(app):
    client = app.test_client()
    assert client.get("/Marti/api/missions/op/changes").status_code == 401
    r = client.get("/Marti/api/missions/guid/guid-op/changes", headers=auth(app, READER))
    assert r.status_code == 200 and r.json["type"] == "MissionChange" and r.json["data"] == []
    assert client.get("/Marti/api/missions/guid/nope/changes").status_code == 404

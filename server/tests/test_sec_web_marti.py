"""Security fixes on the Marti API (raven/marti_auth.py and the Marti blueprints):

* contacts, CoT, client endpoint and track export routes need a client cert or a web session
* PUT /Marti/api/missions/<name>: only the owner changes an existing mission, the creatorUid must
  be the caller's own EUD, and the token in the response is only ever for that EUD
* mission tokens expire
* mission package uploads can't make the server decompress oversize entries
* deactivated users' certs are refused

Minimal sqlite Flask app (as tests/test_mission_features.py): no RabbitMQ, Postgres or nginx.
Client certs are faked by monkeypatching verify_client_cert.
"""

import datetime
import importlib
import io
import time
import zipfile

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

marti_api_module = importlib.import_module("raven.blueprints.marti_api.marti_api")
mission_module = importlib.import_module("raven.blueprints.marti_api.mission_marti_api")
dp_marti = importlib.import_module("raven.blueprints.marti_api.data_package_marti_api")
contacts_module = importlib.import_module("raven.blueprints.marti_api.contacts_marti_api")
cot_module = importlib.import_module("raven.blueprints.marti_api.cot_marti_api")
from raven.models.CoT import CoT  # noqa: E402
from raven.models.EUD import EUD  # noqa: E402
from raven.models.Mission import Mission  # noqa: E402
from raven.models.MissionRole import MissionRole  # noqa: E402


class _FakeCert:
    def __init__(self, cn):
        self._cn = cn

    def get_subject(self):
        cn = self._cn
        return type(
            "Subject",
            (),
            {"commonName": cn, "get_components": lambda self: [(b"CN", cn.encode())]},
        )()


@pytest.fixture
def app(tmp_path):
    ca = tmp_path / "ca"
    (ca / "certs" / "raven").mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (ca / "certs" / "raven" / "raven.nopass.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (ca / "certs" / "raven" / "raven.pub").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    uploads = tmp_path / "uploads"
    uploads.mkdir()

    app = Flask("raven-test")
    app.config.from_object(DefaultConfig)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        RAVEN_DATA_FOLDER=str(tmp_path),
        RAVEN_CA_FOLDER=str(ca),
        UPLOAD_FOLDER=str(uploads),
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
    db.init_app(app)
    babel.init_app(app)

    from raven.models.role import Role
    from raven.models.user import User
    from raven.models.WebAuthn import WebAuthn

    app.security = Security(app, SQLAlchemyUserDatastore(db, User, Role, WebAuthn))
    init_audit(app)
    app.register_blueprint(marti_api_module.marti_api)
    app.register_blueprint(contacts_module.contacts_api)
    app.register_blueprint(cot_module.cot_marti_api)
    app.register_blueprint(mission_module.mission_marti_api)

    with app.app_context():
        db.create_all()
        ds = app.security.datastore
        ds.find_or_create_role("administrator")
        ds.find_or_create_role("user")
        for name, role in (("admin", "administrator"), ("alice", "user"), ("bob", "user"),
                           ("carol", "user")):
            ds.create_user(username=name, password=hash_password("x"), roles=[role])
        db.session.commit()
        for name in ("admin", "alice", "bob", "carol"):
            eud = EUD()
            eud.uid = f"ANDROID-{name}"
            eud.callsign = name.upper()
            eud.user_id = ds.find_user(username=name).id
            db.session.add(eud)
        cot = CoT()
        cot.uid = "ANDROID-alice"
        cot.xml = '<event uid="ANDROID-alice" type="a-f-G"/>'
        now = datetime.datetime.now(datetime.timezone.utc)
        cot.timestamp = cot.start = now
        cot.stale = now + datetime.timedelta(minutes=5)
        db.session.add(cot)
        mission = Mission()
        mission.name = "op"
        mission.guid = "guid-op"
        mission.creator_uid = "ANDROID-alice"
        mission.tool = "public"
        mission.default_role = MissionRole.MISSION_SUBSCRIBER
        db.session.add(mission)
        db.session.commit()
        for uid, username, role in (
            ("ANDROID-alice", "alice", MissionRole.MISSION_OWNER),
            ("ANDROID-bob", "bob", MissionRole.MISSION_SUBSCRIBER),
        ):
            mission_role = MissionRole()
            mission_role.clientUid = uid
            mission_role.username = username
            mission_role.role_type = role
            mission_role.mission_name = "op"
            mission_role.createTime = datetime.datetime.now(datetime.timezone.utc)
            db.session.add(mission_role)
        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def as_cert(monkeypatch):
    def use(cn):
        fake = (lambda: _FakeCert(cn)) if cn else (lambda: False)
        monkeypatch.setattr(marti_api_module, "verify_client_cert", fake)
        monkeypatch.setattr(mission_module, "verify_client_cert", fake)
        monkeypatch.setattr(dp_marti, "verify_client_cert", fake)

    return use


DATA_ROUTES = (
    "/Marti/api/contacts/all",
    "/Marti/api/cot/xml/ANDROID-alice",
    "/Marti/api/cot/xml/ANDROID-alice/all",
    "/Marti/api/clientEndPoints",
    "/Marti/ExportMissionKML?uid=ANDROID-alice",
)


@pytest.mark.parametrize("path", DATA_ROUTES)
def test_marti_data_routes_need_a_cert(app, as_cert, path):
    as_cert(None)
    assert app.test_client().get(path).status_code == 401


@pytest.mark.parametrize("path", DATA_ROUTES[:4])
def test_marti_data_routes_answer_a_cert(app, as_cert, path):
    as_cert("bob")
    assert app.test_client().get(path).status_code == 200


def test_marti_data_routes_answer_a_web_session(app, as_cert):
    as_cert(None)
    with app.app_context():
        uniquifier = app.security.datastore.find_user(username="bob").fs_uniquifier
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()
    assert client.get("/Marti/api/contacts/all").status_code == 200


def test_deactivated_users_cert_is_refused(app, as_cert):
    with app.app_context():
        ds = app.security.datastore
        ds.deactivate_user(ds.find_user(username="bob"))
        db.session.commit()
    as_cert("bob")
    client = app.test_client()
    assert client.get("/Marti/api/contacts/all").status_code == 401
    assert client.get("/Marti/api/missions").status_code == 403
    with app.test_request_context("/"):
        assert dp_marti.data_package_caller() is None


def test_enrollment_and_version_routes_stay_open(app, as_cert):
    as_cert(None)
    assert app.test_client().get("/Marti/api/version/config").status_code == 200


def _put(app, uid, mission="op", **args):
    return app.test_client().put(
        f"/Marti/api/missions/{mission}", query_string={"creatorUid": uid, **args}
    )


def test_subscriber_cannot_change_existing_mission(app, as_cert):
    as_cert("bob")
    response = _put(app, "ANDROID-bob", tool="citrap", defaultRole="MISSION_OWNER")
    assert response.status_code == 403
    with app.app_context():
        mission = db.session.query(Mission).filter_by(name="op").one()
        assert mission.tool == "public"
        assert mission.default_role == MissionRole.MISSION_SUBSCRIBER


def test_cannot_act_as_someone_elses_eud(app, as_cert):
    as_cert("bob")
    assert _put(app, "ANDROID-alice", description="x").status_code == 403
    assert _put(app, "ANDROID-alice", mission="new-one").status_code == 403


def test_owner_changes_mission_and_gets_own_token(app, as_cert):
    as_cert("alice")
    response = _put(app, "ANDROID-alice", description="updated")
    assert response.status_code == 200
    token = response.json["data"][0]["token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == "ANDROID-alice"
    assert claims["exp"] > time.time() + 29 * 86400


def test_admin_token_is_for_admins_own_eud(app, as_cert):
    as_cert("admin")
    response = _put(app, "ANDROID-admin", description="by admin")
    assert response.status_code == 200
    claims = jwt.decode(response.json["data"][0]["token"], options={"verify_signature": False})
    # Never the owner's (mission.creator_uid) token
    assert claims["sub"] == "ANDROID-admin"


def test_expired_mission_token_is_refused(app):
    with app.test_request_context(
        "/", headers={"Authorization": "Bearer placeholder"}
    ):
        mission = Mission()
        mission.name = "op"
        mission.guid = "guid-op"
        app.config["RAVEN_MISSION_TOKEN_DAYS"] = 30
        fresh = mission_module.generate_token(mission, "ANDROID-alice")
    with app.test_request_context("/", headers={"Authorization": f"Bearer {fresh}"}):
        assert mission_module.verify_token()["sub"] == "ANDROID-alice"

    key = open(f"{app.config['RAVEN_CA_FOLDER']}/certs/raven/raven.nopass.key").read()
    expired = jwt.encode(
        {"sub": "ANDROID-alice", "MISSION_NAME": "op", "exp": int(time.time()) - 10},
        key,
        algorithm="RS256",
    )
    with app.test_request_context("/", headers={"Authorization": f"Bearer {expired}"}):
        assert mission_module.verify_token() is False


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


MANIFEST = (
    b'<MissionPackageManifest version="2"><Configuration><Parameter name="uid" value="pkg"/>'
    b'</Configuration><Contents><Content zipEntry="a/item.cot"/></Contents>'
    b"</MissionPackageManifest>"
)


def test_mission_package_normal_size_parses():
    data = _zip({"MANIFEST/manifest.xml": MANIFEST, "a/item.cot": b'<event uid="item-1"/>'})
    uid, items = mission_module._parse_mission_package_map_items(data)
    assert uid == "pkg"
    assert [i[0] for i in items] == ["item-1"]


def test_mission_package_zip_bomb_is_refused(monkeypatch):
    monkeypatch.setattr(mission_module, "MAX_MISSION_PACKAGE_ENTRY_BYTES", 1024)
    bomb = _zip({"MANIFEST/manifest.xml": MANIFEST, "a/item.cot": b"\0" * 4096})
    with pytest.raises(mission_module.MissionPackageTooLarge):
        mission_module._parse_mission_package_map_items(bomb)
    with pytest.raises(mission_module.MissionPackageTooLarge):
        mission_module._parse_mission_package_map_items(
            _zip({"MANIFEST/manifest.xml": MANIFEST + b" " * 2048})
        )


def test_mission_package_total_budget(monkeypatch):
    monkeypatch.setattr(mission_module, "MAX_MISSION_PACKAGE_TOTAL_BYTES", 600)
    data = _zip({"MANIFEST/manifest.xml": MANIFEST, "a/item.cot": b"<event uid='x'/>" + b" " * 600})
    with pytest.raises(mission_module.MissionPackageTooLarge):
        mission_module._parse_mission_package_map_items(data)

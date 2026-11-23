"""Security fixes on the web API:

* MediaMTX path settings: non-administrators only set allowlisted keys (any capitalisation)
* video streams, markers, CASEVACs: only the owner or an administrator changes/deletes them
* stream deletion never removes a recording file outside the recordings folder
* VideoStream.to_json hides source credentials from other users
* /api/users carries no auth tokens; /api/me still has the caller's own
* /oauth/token refuses deactivated users and users who must change their password
* lockout after repeated failed sign-ins (web login, /oauth/token, video credentials)
* deleting a user revokes their certificates; deactivating disconnects their sessions
* /api/certificate lists only your own certificates unless you're an administrator
* /api/user/assign_eud can't claim someone else's or an arbitrary unassigned EUD
* admin write routes enforce CSRF; /api CORS no longer echoes arbitrary origins

Minimal sqlite Flask app: no RabbitMQ, Postgres or MediaMTX.
"""

import datetime
import importlib
import json
import time
from unittest import mock

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

from raven.audit import init_audit  # noqa: E402
from raven.login_lockout import init_lockout, is_locked, register_failure  # noqa: E402

mediamtx_api = importlib.import_module("raven.blueprints.raven_api.mediamtx_api")
user_api = importlib.import_module("raven.blueprints.raven_api.user_api")
token_api = importlib.import_module("raven.blueprints.raven_api.token_api")
api_module = importlib.import_module("raven.blueprints.raven_api.api")
marker_api = importlib.import_module("raven.blueprints.raven_api.marker_api")
casevac_api = importlib.import_module("raven.blueprints.raven_api.casevac_api")
from raven.models.AuditEvent import AuditEvent  # noqa: E402
from raven.models.CasEvac import CasEvac  # noqa: E402
from raven.models.Certificate import Certificate  # noqa: E402
from raven.models.EUD import EUD  # noqa: E402
from raven.models.Marker import Marker  # noqa: E402
from raven.models.VideoRecording import VideoRecording  # noqa: E402
from raven.models.VideoStream import VideoStream, redact_source_url  # noqa: E402


def _cert(common_name, **fields):
    """A Certificate row with the columns the schema requires filled in."""
    cert = Certificate()
    cert.common_name = common_name
    cert.username = common_name
    cert.expiration_date = datetime.datetime(2030, 1, 1)
    cert.server_address = "tak.example.com"
    cert.server_port = 8089
    cert.truststore_filename = "truststore.p12"
    cert.user_cert_filename = f"{common_name}.p12"
    for key, value in fields.items():
        setattr(cert, key, value)
    return cert


CAM = "rtsp://camuser:campass@10.0.0.9:554/live?passphrase=secret&x=1"


def _make_app(tmp_path, csrf=False):
    app = Flask("raven-test")
    app.config.from_object(DefaultConfig)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        RAVEN_DATA_FOLDER=str(tmp_path),
        RAVEN_CA_FOLDER=str(tmp_path / "ca"),
        RAVEN_NODE_ID="node-1",
        RAVEN_ENABLE_LDAP=False,
        RAVEN_MEDIAMTX_API_ADDRESS="http://mediamtx.invalid:9997",
        SECURITY_TWO_FACTOR=False,
        SECURITY_PASSWORD_HASH="plaintext",
        SECURITY_USER_IDENTITY_ATTRIBUTES=[
            {"username": {"mapper": lambda x: x, "case_insensitive": True}}
        ],
    )
    if not csrf:
        app.config.update(SECURITY_CSRF_PROTECT_MECHANISMS=[], WTF_CSRF_ENABLED=False)
    db.init_app(app)
    babel.init_app(app)

    from raven.models.role import Role
    from raven.models.user import User
    from raven.models import import_all_models
    from raven.models.WebAuthn import WebAuthn

    import_all_models()
    if csrf:
        # As in raven/app.py: CSRFProtect must exist before Flask-Security initialises
        import flask_wtf

        flask_wtf.CSRFProtect(app)
    app.security = Security(app, SQLAlchemyUserDatastore(db, User, Role, WebAuthn))
    init_audit(app)
    init_lockout(app)
    app.register_blueprint(mediamtx_api.mediamtx_api_blueprint)
    app.register_blueprint(user_api.user_api_blueprint)
    app.register_blueprint(token_api.token_api_blueprint)
    app.register_blueprint(api_module.api_blueprint)
    app.register_blueprint(marker_api.marker_api_blueprint)
    app.register_blueprint(casevac_api.casevac_api_blueprint)

    with app.app_context():
        db.create_all()
        ds = app.security.datastore
        ds.find_or_create_role("administrator")
        ds.find_or_create_role("user")
        for name, role in (("admin", "administrator"), ("alice", "user"), ("bob", "user")):
            ds.create_user(username=name, password=hash_password("goodpassword"), roles=[role])
        db.session.commit()
        for name in ("alice", "bob"):
            eud = EUD()
            eud.uid = f"ANDROID-{name}"
            eud.callsign = name.upper()
            eud.user_id = ds.find_user(username=name).id
            db.session.add(eud)
        stray = EUD()
        stray.uid = "ANDROID-stray"
        stray.callsign = "STRAY"
        db.session.add(stray)
        for path, owner in (("alice_cam", "alice"), ("bob_cam", "bob")):
            stream = VideoStream()
            stream.path = path
            stream.username = owner
            stream.mediamtx_settings = json.dumps({"source": CAM, "record": False})
            db.session.add(stream)
        db.session.commit()
    return app


@pytest.fixture
def app(tmp_path):
    app = _make_app(tmp_path)
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def web_client(app, username):
    with app.app_context():
        uniquifier = app.security.datastore.find_user(username=username).fs_uniquifier
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()
    return client


class _MediaMTX:
    """requests.post/patch/delete stand-in that records what Raven sent MediaMTX."""

    def __init__(self):
        self.sent = []

    def __call__(self, url, json=None, **kwargs):
        self.sent.append((url, json))
        return mock.Mock(status_code=200, text="", json=lambda: {})


@pytest.fixture
def mediamtx(monkeypatch):
    fake = _MediaMTX()
    for method in ("post", "patch", "delete"):
        monkeypatch.setattr(mediamtx_api.requests, method, fake)
    return fake


# --- MediaMTX path settings ------------------------------------------------------------------


@pytest.mark.parametrize("key", ["runOnReady", "RunOnReady", "RUNONINIT", "recordpath", "RecordPath",
                                 "rpiCameraTuningFile"])
def test_user_cannot_set_dangerous_path_settings(app, mediamtx, key):
    client = web_client(app, "alice")
    r = client.post(
        "/api/mediamtx/stream/add",
        json={"path": "alice_new", "source": "rtsp://10.0.0.1/x", key: "touch /tmp/pwned"},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    sent = mediamtx.sent[-1][1]
    assert not any(k.lower() == key.lower() and v for k, v in sent.items()), sent


def test_user_allowlisted_settings_any_case(app, mediamtx):
    client = web_client(app, "alice")
    r = client.patch("/api/mediamtx/stream/update", json={"path": "alice_cam", "RECORD": True})
    assert r.status_code == 200
    assert mediamtx.sent[-1][1]["record"] is True


def test_admin_keeps_run_on_settings(app, mediamtx):
    client = web_client(app, "admin")
    r = client.patch("/api/mediamtx/stream/update", json={"path": "alice_cam", "runOnReady": "x"})
    assert r.status_code == 200
    assert mediamtx.sent[-1][1]["runOnReady"] == "x"


def test_stream_update_and_delete_need_owner(app, mediamtx):
    client = web_client(app, "bob")
    assert client.patch(
        "/api/mediamtx/stream/update", json={"path": "alice_cam", "record": True}
    ).status_code == 403
    assert client.delete("/api/mediamtx/stream/delete?path=alice_cam").status_code == 403
    assert client.delete("/api/mediamtx/stream/delete?path=bob_cam").status_code == 200
    with app.app_context():
        assert db.session.get(VideoStream, "alice_cam") is not None


def test_stream_delete_never_removes_files_outside_recordings(app, mediamtx, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    inside_dir = tmp_path / "mediamtx" / "recordings" / "bob_cam"
    inside_dir.mkdir(parents=True)
    inside = inside_dir / "seg.mp4"
    inside.write_bytes(b"x")
    with app.app_context():
        for i, path in enumerate((str(victim), str(inside))):
            rec = VideoRecording()
            rec.path = "bob_cam"
            rec.segment_path = path
            rec.in_progress = False
            db.session.add(rec)
        db.session.commit()
    r = web_client(app, "bob").delete("/api/mediamtx/stream/delete?path=bob_cam")
    assert r.status_code == 200
    assert victim.exists()
    assert not inside.exists()


def test_source_credentials_hidden_from_other_users(app):
    with app.app_context():
        stream = db.session.get(VideoStream, "alice_cam")
        for username, visible in (("bob", False), ("alice", True), ("admin", True)):
            with app.test_request_context("/"):
                from flask_login import login_user

                login_user(app.security.datastore.find_user(username=username))
                source = stream.to_json()["source"]
                assert ("campass" in source) is visible, (username, source)
                if not visible:
                    assert "secret" not in source
                    assert "10.0.0.9:554/live" in source


def test_redact_source_url():
    assert redact_source_url("srt://h:1?streamid=a&passphrase=p") == "srt://h:1?streamid=a&passphrase=***"
    assert redact_source_url("rtsp://u:p@h/x") == "rtsp://***@h/x"
    assert redact_source_url(None) is None


# --- Markers and CASEVAC -----------------------------------------------------------------------

MARKER_UID = "1b0c8a52-6f6c-4a7e-9a0e-2b3c4d5e6f70"
CASEVAC_UID = "2b0c8a52-6f6c-4a7e-9a0e-2b3c4d5e6f71"


def _add_items(app):
    from raven.models.Point import Point

    with app.app_context():
        point = Point()
        point.uid = MARKER_UID
        point.device_uid = "ANDROID-alice"
        point.latitude = point.longitude = 1.0
        point.timestamp = datetime.datetime.now(datetime.timezone.utc)
        db.session.add(point)
        db.session.flush()
        marker = Marker()
        marker.uid = MARKER_UID
        marker.parent_uid = "ANDROID-alice"
        marker.point_id = point.id
        db.session.add(marker)
        casevac = CasEvac()
        casevac.uid = CASEVAC_UID
        casevac.sender_uid = "ANDROID-alice"
        casevac.timestamp = datetime.datetime.now(datetime.timezone.utc)
        casevac.title = "CASEVAC 1"
        db.session.add(casevac)
        db.session.commit()


def test_marker_and_casevac_delete_need_owner(app, monkeypatch):
    _add_items(app)
    routed = []
    monkeypatch.setattr(casevac_api, "route_cot", lambda *a, **k: routed.append(a))
    bob = web_client(app, "bob")
    assert bob.delete(f"/api/markers?uid={MARKER_UID}").status_code == 403
    assert bob.delete(f"/api/casevac?uid={CASEVAC_UID}").status_code == 403
    assert bob.post(
        "/api/markers", json={"uid": MARKER_UID, "name": "x", "latitude": 1, "longitude": 1}
    ).status_code == 403
    with app.app_context():
        assert db.session.query(Marker).filter_by(uid=MARKER_UID).first() is not None
        assert db.session.query(CasEvac).filter_by(uid=CASEVAC_UID).first() is not None


def test_casevac_owner_can_delete(app, monkeypatch):
    _add_items(app)
    monkeypatch.setattr(casevac_api, "route_cot", lambda *a, **k: None)
    from raven.models.CoT import CoT

    with app.app_context():
        cot = CoT()
        cot.uid = CASEVAC_UID
        cot.type = "b-r-f-h-c"
        cot.xml = "<event/>"
        cot.timestamp = cot.start = cot.stale = datetime.datetime.now(datetime.timezone.utc)
        db.session.add(cot)
        db.session.commit()
        casevac = db.session.query(CasEvac).filter_by(uid=CASEVAC_UID).one()
        casevac.cot_id = cot.id
        db.session.commit()
    assert web_client(app, "alice").delete(f"/api/casevac?uid={CASEVAC_UID}").status_code == 200


# --- Users and tokens ----------------------------------------------------------------------------


def test_user_listing_has_no_tokens_but_me_does(app):
    admin = web_client(app, "admin")
    r = admin.get("/api/users?include_federated=true")
    assert r.status_code == 200
    assert r.json["results"]
    assert all("token" not in user for user in r.json["results"])
    me = admin.get("/api/me")
    assert me.status_code == 200 and me.json["token"]


def test_token_max_age_is_set():
    assert DefaultConfig.SECURITY_TOKEN_MAX_AGE == 30 * 24 * 60 * 60
    assert DefaultConfig.SECURITY_PASSWORD_LENGTH_MIN >= 9


@pytest.fixture
def oauth_key(app, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    folder = tmp_path / "ca" / "certs" / "raven"
    folder.mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (folder / "raven.nopass.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def _oauth(app, username, password="goodpassword"):
    return app.test_client().post(
        "/oauth/token", data={"username": username, "password": password}
    )


def test_oauth_token_ok_then_refused_for_inactive_and_forced_change(app, oauth_key):
    assert _oauth(app, "alice").status_code == 200
    with app.app_context():
        ds = app.security.datastore
        ds.deactivate_user(ds.find_user(username="alice"))
        bob = ds.find_user(username="bob")
        bob.force_password_change = True
        db.session.commit()
    assert _oauth(app, "alice").status_code == 400
    assert _oauth(app, "bob").status_code == 403


def test_oauth_lockout_after_ten_failures(app, oauth_key):
    for _ in range(10):
        assert _oauth(app, "alice", "wrong").status_code == 400
    # Right password, but locked
    assert _oauth(app, "alice").status_code == 429
    with app.app_context():
        locks = db.session.query(AuditEvent).filter_by(action="account_locked").all()
        assert len(locks) == 1 and locks[0].username == "alice"
    # Another username isn't affected
    assert _oauth(app, "bob").status_code == 200


def test_lock_expires(app, oauth_key):
    for _ in range(10):
        _oauth(app, "alice", "wrong")
    with app.app_context():
        # The failures and the lock all happened 16 minutes ago
        for event in db.session.query(AuditEvent).filter_by(username="alice").all():
            event.timestamp = event.timestamp - datetime.timedelta(minutes=16)
        db.session.commit()
        with app.test_request_context("/"):
            assert not is_locked("alice")
            # Attempts before (and during) the expired lock don't count again
            assert register_failure("alice") is False
    assert _oauth(app, "alice").status_code == 200


def _login(client, username, password):
    return client.post("/api/login", json={"username": username, "password": password})


def test_web_login_lockout(app):
    client = app.test_client()
    for _ in range(10):
        assert _login(client, "Alice", "wrong").status_code == 400
    r = _login(client, "alice", "goodpassword")
    assert r.status_code == 429
    assert "Too many failed" in r.json["response"]["errors"][0]
    assert _login(app.test_client(), "bob", "goodpassword").status_code == 200


def test_successful_login_resets_count(app):
    client = app.test_client()
    for _ in range(9):
        _login(client, "alice", "wrong")
    assert _login(app.test_client(), "alice", "goodpassword").status_code == 200
    for _ in range(9):
        _login(app.test_client(), "alice", "wrong")
    assert _login(app.test_client(), "alice", "goodpassword").status_code == 200


def test_video_credential_failures_do_not_lock_the_account(app):
    """A camera retrying an old RTSP password must not lock its owner out."""
    with app.test_request_context("/"):
        for _ in range(12):
            assert mediamtx_api._user_from_password("alice", "wrong") is None
        assert not is_locked("alice")
        assert mediamtx_api._user_from_password("alice", "goodpassword") is not None
        with app.app_context():
            assert db.session.query(AuditEvent).filter_by(action="video_login_failed").count() == 12


def test_locked_account_cannot_publish_video(app, oauth_key):
    for _ in range(10):
        _oauth(app, "alice", "wrong")
    with app.test_request_context("/"):
        assert mediamtx_api._user_from_password("alice", "goodpassword") is None


def test_ownerless_stream_still_manageable(app, mediamtx):
    """Streams shared from ATAK (/Marti/vcm) have no owner recorded; users keep managing them."""
    with app.app_context():
        stream = VideoStream()
        stream.path = "atak_shared"
        stream.username = None
        stream.mediamtx_settings = json.dumps({"source": "rtsp://10.0.0.5/x", "record": False})
        db.session.add(stream)
        db.session.commit()
    bob = web_client(app, "bob")
    assert bob.patch("/api/mediamtx/stream/update", json={"path": "atak_shared", "record": True}).status_code == 200


def test_casevac_edit_keeps_device_owner(app, monkeypatch):
    _add_items(app)
    monkeypatch.setattr(casevac_api, "route_cot", lambda *a, **k: None)
    monkeypatch.setattr(casevac_api, "socketio", mock.Mock())
    from raven.models.Icon import Icon

    with app.app_context():
        icon = Icon()
        icon.filename = "red_crs.png"
        icon.bitmap = b""
        db.session.add(icon)
        db.session.commit()
    alice = web_client(app, "alice")
    body = {"uid": CASEVAC_UID, "title": "CASEVAC 1", "latitude": 1, "longitude": 1,
            "timestamp": "2026-11-15T01:00:00.000Z"}
    r = alice.post("/api/casevac", json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        assert db.session.query(CasEvac).filter_by(uid=CASEVAC_UID).one().sender_uid == "ANDROID-alice"


# --- Deleting / deactivating users -----------------------------------------------------------------


def test_delete_user_revokes_certificates(app, monkeypatch):
    calls = []
    from raven.blueprints.raven_api import certificate_api

    monkeypatch.setattr(
        certificate_api, "revoke_user_certificates",
        lambda username, reason, actor, note=None: calls.append((username, reason, actor)),
    )
    r = web_client(app, "admin").post("/api/user/delete", json={"username": "bob"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert calls == [("bob", "cessationOfOperation", "admin")]


def test_deactivate_user_disconnects_cert_sessions(app, monkeypatch):
    import raven.cert_revocation as cert_revocation

    kicked = []
    monkeypatch.setattr(
        cert_revocation, "kick_revoked_sessions",
        lambda app_, serials, logger=None: kicked.append(sorted(serials)) or {"sent": True},
    )
    monkeypatch.setattr(user_api.pika, "BlockingConnection", mock.Mock(side_effect=OSError))
    with app.app_context():
        for serial, revoked in (("AA01", None), ("AA02", datetime.datetime(2026, 1, 1))):
            db.session.add(_cert("bob", serial_number=serial, revoked_at=revoked))
        db.session.commit()
    r = web_client(app, "admin").post("/api/user/deactivate", json={"username": "bob"})
    assert r.status_code == 200
    assert kicked == [["AA01"]]


# --- Certificates and EUD assignment -------------------------------------------------------------


def test_certificate_listing_is_own_only(app):
    with app.app_context():
        for name in ("alice", "bob"):
            db.session.add(_cert(name))
        db.session.commit()
    r = web_client(app, "alice").get("/api/certificate")
    assert r.status_code == 200
    assert {c["user_cert_filename"] for c in r.json["results"]} == {"alice.p12"}
    r = web_client(app, "admin").get("/api/certificate")
    assert {c["user_cert_filename"] for c in r.json["results"]} == {"alice.p12", "bob.p12"}


def test_assign_eud_restrictions(app):
    alice = web_client(app, "alice")
    assert alice.post("/api/user/assign_eud", json={"uid": "ANDROID-bob"}).status_code == 403
    assert alice.post(
        "/api/user/assign_eud", json={"uid": "ANDROID-alice", "username": "bob"}
    ).status_code == 403
    # Unassigned, but no certificate was issued to alice for it
    assert alice.post("/api/user/assign_eud", json={"uid": "ANDROID-stray"}).status_code == 403
    with app.app_context():
        db.session.add(_cert("alice", eud_uid="ANDROID-stray"))
        db.session.commit()
    assert alice.post("/api/user/assign_eud", json={"uid": "ANDROID-stray"}).status_code == 200
    assert web_client(app, "admin").post(
        "/api/user/assign_eud", json={"uid": "ANDROID-bob", "username": "alice"}
    ).status_code == 200


# --- CSRF and CORS ---------------------------------------------------------------------------------


def test_admin_write_routes_enforce_csrf(tmp_path):
    app = _make_app(tmp_path, csrf=True)
    client = web_client(app, "admin")
    r = client.post("/api/user/role", json={"username": "bob", "roles": ["administrator"]})
    assert r.status_code == 400
    with app.app_context():
        assert not app.security.datastore.find_user(username="bob").has_role("administrator")
    token = client.get("/api/login", headers={"Accept": "application/json"}).json["response"]["csrf_token"]
    r = client.post(
        "/api/user/role",
        json={"username": "bob", "roles": ["administrator"]},
        headers={"X-XSRF-Token": token},
    )
    assert r.status_code == 200, r.get_data(as_text=True)


def test_api_cors_origins():
    from raven.app import api_cors_origins

    origins = api_cors_origins({"RAVEN_DOMAIN": "tak.example.com", "RAVEN_CORS_ORIGINS": ["*", "https://x.test/"]})
    assert "https://tak.example.com" in origins
    assert "https://x.test" in origins
    assert "*" not in origins
    assert api_cors_origins({}) == []

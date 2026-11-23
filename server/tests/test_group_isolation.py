"""Group isolation for data packages (raven/group_isolation.py); video streams have none.

Minimal sqlite Flask app, as in tests/test_group_policies.py: no RabbitMQ,
Postgres or MediaMTX. Marti requests are authenticated by monkeypatching
verify_client_cert, the same way tests/test_device_profiles.py does.
"""

import datetime
import io
import json
import time
import zipfile

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
import importlib  # noqa: E402

# The package __init__ re-exports the blueprints under the module names
dp_marti = importlib.import_module("raven.blueprints.marti_api.data_package_marti_api")
video_marti = importlib.import_module("raven.blueprints.marti_api.video_marti_api")
from raven.blueprints.raven_api.data_package_api import data_package_api  # noqa: E402
from raven.blueprints.raven_api.video_api import video_api_blueprint  # noqa: E402
from raven.models.AuditEvent import AuditEvent  # noqa: E402
from raven.models.DataPackage import DataPackage  # noqa: E402
from raven.models.Group import Group  # noqa: E402
from raven.models.GroupUser import GroupUser  # noqa: E402
from raven.models.VideoRecording import VideoRecording  # noqa: E402
from raven.models.VideoStream import VideoStream  # noqa: E402


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
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    app = Flask("raven-test")
    app.config.from_object(DefaultConfig)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        RAVEN_DATA_FOLDER=str(tmp_path),
        UPLOAD_FOLDER=str(uploads),
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
    app.register_blueprint(data_package_api)
    app.register_blueprint(dp_marti.data_package_marti_api)
    app.register_blueprint(video_api_blueprint)
    app.register_blueprint(video_marti.video_marti_api)

    with app.app_context():
        db.create_all()
        ds = app.security.datastore
        ds.find_or_create_role("administrator")
        ds.find_or_create_role("user")
        for name, role in (("admin", "administrator"), ("alice", "user"), ("ann", "user"),
                           ("bob", "user")):
            ds.create_user(username=name, password=hash_password("x"), roles=[role])
        db.session.commit()

        # alice and ann are in red (both directions), bob in blue. ann only *sends* to
        # green, so her uploads are tagged green but she can't read green items.
        for group_name in ("red", "blue", "green"):
            group = Group()
            group.name = group_name
            group.type = Group.SYSTEM
            db.session.add(group)
            db.session.commit()
        memberships = {
            "alice": [("red", "IN"), ("red", "OUT")],
            "ann": [("red", "IN"), ("red", "OUT"), ("green", "IN")],
            "bob": [("blue", "IN"), ("blue", "OUT")],
        }
        for username, rows in memberships.items():
            user = ds.find_user(username=username)
            for group_name, direction in rows:
                group = db.session.query(Group).filter_by(name=group_name).one()
                db.session.add(
                    GroupUser(user_id=user.id, group_id=group.id, direction=direction, enabled=True)
                )
        db.session.commit()

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


@pytest.fixture
def as_cert(monkeypatch):
    """Makes Marti requests carry a verified client cert for the given CN (None = no cert)."""

    def use(cn):
        fake = (lambda: _FakeCert(cn)) if cn else (lambda: False)
        monkeypatch.setattr(dp_marti, "verify_client_cert", fake)
        monkeypatch.setattr(video_marti, "verify_client_cert", fake)

    return use


def zip_bytes(content=b"hello"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("MANIFEST/manifest.xml", content)
    return buf.getvalue()


def marti_upload(app, name, content):
    """ATAK-style upload over the cert port (no web session)."""
    r = app.test_client().post(
        "/Marti/sync/missionupload",
        data={"assetfile": (io.BytesIO(zip_bytes(content)), name, "application/x-zip-compressed")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.data
    return r.data.decode().split("/metadata/")[1].split("/")[0]


def legacy_package(app, file_hash="legacyhash"):
    with app.app_context():
        dp = DataPackage()
        dp.filename = "legacy.zip"
        dp.hash = file_hash
        dp.submission_time = datetime.datetime.now(datetime.timezone.utc)
        dp.mime_type = "application/zip"
        dp.size = 1
        db.session.add(dp)
        db.session.commit()
    with open(f"{app.config['UPLOAD_FOLDER']}/{file_hash}.zip", "wb") as f:
        f.write(zip_bytes(b"legacy"))
    return file_hash


def marti_hashes(app, path="/Marti/sync/search"):
    r = app.test_client().get(path)
    assert r.status_code == 200, r.data
    if "results" in r.json:
        return {row["Hash"] for row in r.json["results"]}
    return {row["hash"] for row in r.json["data"]}


# --- data packages ---


def test_marti_upload_gets_uploaders_in_groups(app, as_cert):
    as_cert("ann")
    file_hash = marti_upload(app, "ann.zip", b"ann")
    with app.app_context():
        dp = db.session.query(DataPackage).filter_by(hash=file_hash).one()
        # IN memberships only: ann sends to red and green
        assert dp.group_names == ["green", "red"]
        assert dp.user.username == "ann"

    r = app.test_client().get("/Marti/api/sync/search", query_string={"hash": file_hash})
    assert r.json["data"][0]["groups"] == ["green", "red"]


def test_web_upload_gets_uploaders_groups(app):
    r = web_client(app, "bob").post(
        "/api/data_packages",
        data={"file": (io.BytesIO(zip_bytes(b"bob")), "bob.zip", "application/zip")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.data
    file_hash = r.data.decode().split("/metadata/")[1].split("/")[0]
    with app.app_context():
        assert db.session.query(DataPackage).filter_by(hash=file_hash).one().group_names == [
            "blue"
        ]


def test_cross_group_user_cannot_list_or_download(app, as_cert):
    as_cert("alice")
    file_hash = marti_upload(app, "alice.zip", b"alice")

    # Same group sees and downloads it, over Marti and the web UI
    as_cert("ann")
    assert file_hash in marti_hashes(app)
    assert file_hash in marti_hashes(app, "/Marti/api/sync/search")
    assert app.test_client().get("/Marti/sync/content", query_string={"hash": file_hash}).status_code == 200
    ann = web_client(app, "ann")
    assert file_hash in {d["hash"] for d in ann.get("/api/data_packages").json["results"]}
    assert ann.get("/api/data_packages/download", query_string={"hash": file_hash}).status_code == 200

    # Another group's ATAK client: not listed, not searchable by hash/uid, not downloadable
    as_cert("bob")
    assert file_hash not in marti_hashes(app)
    assert file_hash not in marti_hashes(app, "/Marti/api/files/metadata")
    client = app.test_client()
    assert client.get("/Marti/sync/search", query_string={"uid": file_hash}).json["resultCount"] == 0
    assert client.get("/Marti/api/sync/search", query_string={"hash": file_hash}).json["data"] == []
    assert client.get("/Marti/sync/content", query_string={"hash": file_hash}).status_code == 403
    assert client.head("/Marti/sync/content", query_string={"hash": file_hash}).status_code == 403
    assert client.get(f"/Marti/api/sync/metadata/{file_hash}/tool").status_code == 403
    assert client.get("/Marti/sync/missionquery", query_string={"hash": file_hash}).status_code == 404

    # ...nor through the web UI
    bob = web_client(app, "bob")
    assert file_hash not in {d["hash"] for d in bob.get("/api/data_packages").json["results"]}
    assert bob.get("/api/data_packages/download", query_string={"hash": file_hash}).status_code == 403

    # No cert at all only gets ungrouped packages
    as_cert(None)
    assert file_hash not in marti_hashes(app)


def test_send_only_membership_does_not_grant_read(app, as_cert):
    with app.app_context():
        green = db.session.query(Group).filter_by(name="green").one()
        dp = DataPackage()
        dp.filename = "g.zip"
        dp.hash = "greenhash"
        dp.submission_time = datetime.datetime.now(datetime.timezone.utc)
        dp.mime_type = "application/zip"
        dp.size = 1
        dp.groups = [green]
        db.session.add(dp)
        db.session.commit()
    as_cert("ann")  # green IN only
    assert "greenhash" not in marti_hashes(app)


def test_legacy_package_without_groups_visible_to_everyone(app, as_cert):
    file_hash = legacy_package(app)
    for cn in ("alice", "bob", None):
        as_cert(cn)
        assert file_hash in marti_hashes(app)
        assert app.test_client().get("/Marti/sync/content", query_string={"hash": file_hash}).status_code == 200
    r = web_client(app, "bob").get("/api/data_packages")
    row = next(d for d in r.json["results"] if d["hash"] == file_hash)
    assert row["groups"] == []


def test_admin_sees_all(app, as_cert):
    as_cert("alice")
    file_hash = marti_upload(app, "alice.zip", b"alice")

    as_cert("admin")
    assert file_hash in marti_hashes(app)
    assert app.test_client().get("/Marti/sync/content", query_string={"hash": file_hash}).status_code == 200

    admin = web_client(app, "admin")
    row = next(d for d in admin.get("/api/data_packages").json["results"] if d["hash"] == file_hash)
    assert row["groups"] == ["red"]
    assert admin.get("/api/data_packages/download", query_string={"hash": file_hash}).status_code == 200


def test_admin_edits_groups_and_is_audited(app, as_cert):
    as_cert("alice")
    file_hash = marti_upload(app, "alice.zip", b"alice")

    # Non-admins can't
    r = web_client(app, "alice").patch(
        "/api/data_packages/groups", json={"hash": file_hash, "groups": []}
    )
    assert r.status_code == 403

    admin = web_client(app, "admin")
    assert admin.patch(
        "/api/data_packages/groups", json={"hash": file_hash, "groups": ["nope"]}
    ).status_code == 404
    r = admin.patch("/api/data_packages/groups", json={"hash": file_hash, "groups": ["blue"]})
    assert r.status_code == 200, r.json
    assert r.json["groups"] == ["blue"]

    as_cert("bob")
    assert file_hash in marti_hashes(app)
    # alice uploaded it, so she still sees it
    as_cert("alice")
    assert file_hash in marti_hashes(app)
    as_cert("ann")
    assert file_hash not in marti_hashes(app)

    # Clearing makes it everyone's
    assert admin.patch(
        "/api/data_packages/groups", json={"hash": file_hash, "groups": []}
    ).json["groups"] == []
    assert file_hash in marti_hashes(app)

    with app.app_context():
        events = [
            e.to_json()
            for e in db.session.query(AuditEvent)
            .filter_by(action="data_package_groups_changed")
            .order_by(AuditEvent.id)
        ]
    assert len(events) == 2
    assert events[0]["actor"] == "admin"
    assert "red -> blue" in events[0]["details"]
    assert "blue -> everyone" in events[1]["details"]


# --- video streams: they don't belong to groups ---


def add_stream(app, path):
    with app.app_context():
        v = VideoStream()
        v.path = path
        v.uid = f"uid-{path}"
        v.mediamtx_settings = json.dumps({"source": None, "record": False})
        db.session.add(v)
        db.session.commit()


def marti_video_paths(app):
    r = app.test_client().get("/Marti/api/video")
    assert r.status_code == 200, r.data
    return {v["alias"] for v in r.json["videoConnections"]}


def test_every_user_sees_every_video_stream(app, as_cert):
    add_stream(app, "redcam")
    add_stream(app, "bluecam")

    for username in ("alice", "bob", "admin"):
        as_cert(username)
        assert marti_video_paths(app) == {"redcam", "bluecam"}
        assert app.test_client().get("/Marti/api/video/uid-redcam").status_code == 200
        r = web_client(app, username).get("/api/video_streams", query_string={"per_page": 50})
        assert r.status_code == 200, r.json
        assert {v["path"] for v in r.json["results"]} == {"redcam", "bluecam"}
        assert all("groups" not in v for v in r.json["results"])


def test_marti_video_add_is_visible_to_other_groups(app, as_cert, monkeypatch):
    as_cert("bob")
    body = {"videoConnections": [{"feeds": [{"alias": "Bob Cam", "url": "rtsp://x/y", "uuid": "bob-1"}]}]}
    # Don't try to reach MediaMTX
    monkeypatch.setattr(
        video_marti.requests, "post", lambda *a, **k: type("R", (), {"status_code": 200, "text": ""})()
    )
    r = app.test_client().post("/Marti/api/video", json=body)
    assert r.status_code == 200, r.data
    as_cert("alice")
    assert "Bob Cam" in marti_video_paths(app)


def test_recordings_are_visible_to_every_user(app):
    add_stream(app, "redcam")
    with app.app_context():
        rec = VideoRecording()
        rec.segment_path = "/nonexistent/seg.mp4"
        rec.path = "redcam"
        rec.in_progress = False
        db.session.add(rec)
        db.session.commit()
        rec_id = rec.id

    for username in ("alice", "bob"):
        client = web_client(app, username)
        assert len(client.get("/api/videos/recordings").json["results"]) == 1
        assert client.head("/api/videos/recording", query_string={"id": rec_id}).status_code == 200


def test_video_stream_groups_endpoint_is_gone(app):
    add_stream(app, "redcam")
    r = web_client(app, "admin").patch(
        "/api/video_streams/groups", json={"path": "redcam", "groups": ["red"]}
    )
    assert r.status_code in (404, 405)

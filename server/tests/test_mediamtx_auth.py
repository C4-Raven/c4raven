"""MediaMTX authentication (mediamtx_api.external_auth).

MediaMTX POSTs a JSON body ({user, password, ip, action, path, protocol, id, query}) for every
read, playback and publish; these tests send the same bodies. Minimal sqlite Flask app: no
RabbitMQ, Postgres or MediaMTX (its API is mocked). Video streams don't belong to groups, so
the users' group memberships below must make no difference.
"""

import importlib
import json
import time
from unittest import mock
from urllib.parse import urlparse

import pytest
from bs4 import BeautifulSoup
from flask import Flask
from flask_security import Security, SQLAlchemyUserDatastore, hash_password
from flask_security.models import fsqla_v3 as fsqla

from raven.defaultconfig import DefaultConfig
from raven.extensions import babel, db

try:
    fsqla.FsModels.set_db_info(db)
except Exception:
    pass

# The package __init__ re-exports the blueprints under the module names
mediamtx_api = importlib.import_module("raven.blueprints.raven_api.mediamtx_api")
video_marti = importlib.import_module("raven.blueprints.marti_api.video_marti_api")
from raven.blueprints.raven_api.video_api import video_api_blueprint  # noqa: E402
from raven.models.Group import Group  # noqa: E402
from raven.models.GroupUser import GroupUser  # noqa: E402
from raven.models.VideoStream import VideoStream  # noqa: E402

WEB_COPY = mediamtx_api.WEB_COPY_SUFFIX
SERVER_TOKEN = "server-token"
OUTSIDE_IP = "203.0.113.5"


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
    app = Flask("raven-test")
    app.config.from_object(DefaultConfig)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'test.db'}",
        RAVEN_DATA_FOLDER=str(tmp_path),
        RAVEN_ENABLE_LDAP=False,
        RAVEN_MEDIAMTX_TOKEN=SERVER_TOKEN,
        RAVEN_MEDIAMTX_API_ADDRESS="http://mediamtx.invalid:9997",
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
    app.register_blueprint(mediamtx_api.mediamtx_api_blueprint)
    app.register_blueprint(video_api_blueprint)
    app.register_blueprint(video_marti.video_marti_api)

    with app.app_context():
        db.create_all()
        ds = app.security.datastore
        ds.find_or_create_role("administrator")
        ds.find_or_create_role("user")
        for name, role in (("admin", "administrator"), ("alice", "user"), ("bob", "user"),
                           ("gone", "user")):
            ds.create_user(username=name, password=hash_password("x"), roles=[role])
        ds.find_user(username="gone").active = False
        db.session.commit()

        for group_name in ("red", "blue"):
            group = Group()
            group.name = group_name
            group.type = Group.SYSTEM
            db.session.add(group)
        db.session.commit()
        red = db.session.query(Group).filter_by(name="red").one()
        blue = db.session.query(Group).filter_by(name="blue").one()

        # alice is red, bob is blue: neither changes what they may do with video
        memberships = {
            "alice": [(red, "IN"), (red, "OUT")],
            "bob": [(blue, "IN"), (blue, "OUT")],
            "gone": [(red, "IN"), (red, "OUT")],
        }
        for username, rows in memberships.items():
            user = ds.find_user(username=username)
            for group, direction in rows:
                db.session.add(
                    GroupUser(user_id=user.id, group_id=group.id, direction=direction, enabled=True)
                )

        def stream(path, owner=None, settings=None, ready=False):
            v = VideoStream(
                path=path,
                username=owner,
                ready=ready,
                uid=f"uid-{path}",
                port=8554,
                mediamtx_settings=json.dumps({"source": None, "record": False, **(settings or {})}),
            )
            v.generate_xml("10.0.0.1")
            db.session.add(v)

        stream("alicecam", owner="alice", ready=True)  # live
        stream("opencam")  # no owner
        stream("live/VIDEO_1", owner="bob")  # a common app default path, owned but idle
        stream("aliceyt", owner="alice", settings={"runOnDemand": "ffmpeg ..."})  # MediaMTX's own
        db.session.commit()

    with mock.patch.object(mediamtx_api.requests, "post") as post:
        post.return_value.status_code = 200
        app.mediamtx_post = post
        yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


def user_of(app, username):
    return app.security.datastore.find_user(username=username)


def jwt(app, username):
    with app.app_context():
        return user_of(app, username).get_auth_token()


def auth(app, action, protocol, path, user="", password="", query="", ip=OUTSIDE_IP, headers=None):
    body = {
        "user": user,
        "password": password,
        "ip": ip,
        "action": action,
        "path": path,
        "protocol": protocol,
        "id": "conn-1",
        "query": query,
    }
    return app.test_client().post("/api/external_auth", json=body, headers=headers or {}).status_code


# Each principal as MediaMTX would present it: (user, password, query)
def principal(app, who):
    if who == "anonymous":
        return "", "", ""
    if who == "server":
        return "", "", f"token={SERVER_TOKEN}"
    kind, username = who.split(":")
    if kind == "pw":
        return username, "x", ""
    if kind == "jwt":
        return "", "", f"jwt={jwt(app, username)}"
    raise AssertionError(who)


def owner(app, path):
    with app.app_context():
        return db.session.get(VideoStream, path).username


READ_ACTIONS = [(a, p) for a in ("read", "playback") for p in ("rtsp", "hls", "webrtc")]
STREAMS = ["alicecam", "opencam", "live/VIDEO_1", "aliceyt", "unknown"]


@pytest.mark.parametrize("action,protocol", READ_ACTIONS)
@pytest.mark.parametrize("path", STREAMS)
def test_any_user_watches_any_stream(app, action, protocol, path):
    for who in ("pw:admin", "pw:alice", "jwt:alice", "pw:bob", "jwt:bob", "server"):
        user, password, query = principal(app, who)
        assert auth(app, action, protocol, path, user, password, query) == 200, who
        # The browser copy of a stream is guarded like the stream
        assert auth(app, action, protocol, path + WEB_COPY, user, password, query) == 200, who

    # TAK clients send no RTSP credentials; everything else needs a user
    anonymous = 200 if (action, protocol) == ("read", "rtsp") else 401
    for user, password, query in (("", "", ""), ("", "", "jwt=garbage"), ("gone", "x", "")):
        assert auth(app, action, protocol, path, user, password, query) == anonymous


@pytest.mark.parametrize("protocol", ["rtsp", "rtmp", "webrtc", "srt"])
@pytest.mark.parametrize(
    "path,who,expected",
    [
        ("alicecam", "pw:alice", 200),  # owner
        ("alicecam", "jwt:alice", 200),
        ("alicecam", "pw:admin", 200),
        ("alicecam", "server", 200),
        ("alicecam", "pw:bob", 401),  # someone else's, and live
        ("alicecam", "jwt:bob", 401),
        ("alicecam", "pw:gone", 401),
        ("alicecam", "anonymous", 401),
        ("alicecam" + WEB_COPY, "pw:bob", 401),
        ("alicecam" + WEB_COPY, "server", 200),
        ("opencam", "pw:bob", 200),  # nobody's
        ("opencam", "anonymous", 401),
    ],
)
def test_publishing_onto_an_existing_path(app, protocol, path, who, expected):
    user, password, query = principal(app, who)
    assert auth(app, "publish", protocol, path, user, password, query) == expected
    with app.app_context():
        assert db.session.query(VideoStream).count() == 4  # nothing registered
    assert owner(app, "alicecam") == "alice"
    app.mediamtx_post.assert_not_called()


def test_idle_stream_is_taken_over_by_the_next_publisher(app):
    """Some apps publish every phone to live/VIDEO_1: once bob has stopped, alice can stream."""
    assert auth(app, "publish", "rtsp", "live/VIDEO_1", "alice", "x") == 200
    assert owner(app, "live/VIDEO_1") == "alice"
    app.mediamtx_post.assert_not_called()  # already a MediaMTX path
    # Still idle, so bob can take it back
    assert auth(app, "publish", "rtsp", "live/VIDEO_1", "bob", "x") == 200
    assert owner(app, "live/VIDEO_1") == "bob"


@pytest.mark.parametrize("who", ["anonymous", "pw:gone"])
def test_takeover_needs_a_real_user(app, who):
    user, password, query = principal(app, who)
    assert auth(app, "publish", "rtsp", "live/VIDEO_1", user, password, query) == 401
    assert owner(app, "live/VIDEO_1") == "bob"


def test_live_server_run_and_web_copy_streams_are_not_taken_over(app):
    # Live: alice is publishing right now
    assert auth(app, "publish", "rtsp", "alicecam", "bob", "x") == 401
    # A web copy belongs to Raven, and publishing to one mustn't claim its source
    assert auth(app, "publish", "rtsp", "live/VIDEO_1" + WEB_COPY, "alice", "x") == 401
    assert owner(app, "live/VIDEO_1") == "bob"
    # MediaMTX runs this one's publisher itself
    assert auth(app, "publish", "rtsp", "aliceyt", "bob", "x") == 401
    assert owner(app, "aliceyt") == "alice"


def test_publishing_a_new_path_registers_it(app):
    assert auth(app, "publish", "rtsp", "bobnew", "bob", "x") == 200
    with app.app_context():
        stream = db.session.get(VideoStream, "bobnew")
        assert stream.username == "bob"
        assert stream.uid == "conn-1"
    assert app.mediamtx_post.call_args.args[0].endswith("/v3/config/paths/add/bobnew")
    # Anyone may watch it
    assert auth(app, "read", "rtsp", "bobnew") == 200
    assert auth(app, "read", "hls", "bobnew", "alice", "x") == 200


def test_web_ui_browser_publish(app):
    """VideoStreams.tsx opens :8889/<username>_browser/publish?jwt=<token> (WHIP)."""
    query = f"jwt={jwt(app, 'alice')}"
    assert auth(app, "publish", "webrtc", "alice_browser", query=query) == 200
    assert owner(app, "alice_browser") == "alice"
    # MediaMTX's "ready" webhook marks it live; meanwhile nobody else can hijack it
    with app.app_context():
        db.session.get(VideoStream, "alice_browser").ready = True
        db.session.commit()
    assert auth(app, "publish", "webrtc", "alice_browser", query=query) == 200
    assert auth(app, "publish", "webrtc", "alice_browser", query=f"jwt={jwt(app, 'bob')}") == 401


def test_anonymous_and_bad_credentials_cannot_publish(app):
    for user, password, query in (
        ("", "", ""),
        ("bob", "wrong", ""),
        ("nobody", "x", ""),
        ("", "", "jwt=garbage"),
        ("", "", "token=wrong"),
    ):
        assert auth(app, "publish", "rtsp", "newpath", user, password, query) == 401
    app.mediamtx_post.assert_not_called()


def test_users_cannot_publish_new_web_copy_paths(app):
    assert auth(app, "publish", "rtsp", "elsewhere" + WEB_COPY, "bob", "x") == 401
    assert auth(app, "publish", "rtsp", "elsewhere" + WEB_COPY, query=f"token={SERVER_TOKEN}") == 200


def test_localhost_publisher(app):
    """Uncredentialed publishers from 127.0.0.1: MediaMTX's runOnDemand ffmpeg, but also
    nginx's RTSPS/RTMPS proxies."""
    for path in ("newpath", "opencam", "alicecam", "aliceyt"):
        assert auth(app, "publish", "rtsp", path, ip="127.0.0.1") == 200
    # Never for reading: nginx's HLS/WebRTC proxying comes from 127.0.0.1 too
    assert auth(app, "read", "hls", "opencam", ip="127.0.0.1") == 401


def test_not_reachable_through_nginx(app):
    headers = {"X-Forwarded-For": "198.51.100.7"}
    assert auth(app, "read", "rtsp", "opencam", query=f"token={SERVER_TOKEN}", headers=headers) == 401


def test_api_actions_only_for_admins(app):
    assert auth(app, "api", "", "", "admin", "x") == 200
    assert auth(app, "api", "", "", "bob", "x") == 401
    assert auth(app, "metrics", "", "") == 401


# --- the feed URLs handed out play as they are ---


def test_marti_vcm_feed_paths_are_bare(app, monkeypatch):
    monkeypatch.setattr(video_marti, "verify_client_cert", lambda: _FakeCert("bob"))
    r = app.test_client().get("/Marti/vcm")
    assert r.status_code == 200
    feeds = {
        f.find("uid").text: f.find("path").text
        for f in BeautifulSoup(r.data, "xml").find_all("feed")
    }
    assert feeds["uid-alicecam"] == "alicecam"
    # What ATAK then sends MediaMTX: rtsp://host:8554/alicecam, no credentials
    assert auth(app, "read", "rtsp", "alicecam") == 200


def test_marti_video_list_urls_play(app, monkeypatch):
    monkeypatch.setattr(video_marti, "verify_client_cert", lambda: _FakeCert("bob"))
    r = app.test_client().get("/Marti/api/video")
    assert r.status_code == 200
    urls = {v["uuid"]: v["feeds"][0]["url"] for v in r.json["videoConnections"]}
    assert set(urls) == {"uid-alicecam", "uid-opencam", "uid-live/VIDEO_1", "uid-aliceyt"}
    assert auth(app, "read", "hls", "alicecam", query=urlparse(urls["uid-alicecam"]).query) == 200


def test_web_listing_links_are_bare(app):
    with app.app_context():
        uniquifier = user_of(app, "bob").fs_uniquifier
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()
    r = client.get("/api/video_streams", query_string={"per_page": 50})
    assert r.status_code == 200
    streams = {s["path"]: s for s in r.json["results"]}
    assert set(streams) == {"alicecam", "opencam", "live/VIDEO_1", "aliceyt"}
    assert streams["alicecam"]["rtsp_link"].endswith(":8554/alicecam")
    # The web/HLS links stay bare too: the UI appends ?jwt= itself
    assert "?" not in streams["alicecam"]["hls_link"]
    assert "groups" not in streams["alicecam"]


def _web_client(app, username):
    with app.app_context():
        uniquifier = user_of(app, username).fs_uniquifier
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()
    return client


@pytest.mark.parametrize("record", [True, False])
def test_record_toggle_follows_to_the_web_copy(app, record):
    """MediaMTX can't record an MPEG-TS track, so the stream's web copy records instead."""
    with app.app_context():
        db.session.get(VideoStream, "live/VIDEO_1").web_path = "live/VIDEO_1" + WEB_COPY
        db.session.commit()
    with mock.patch.object(mediamtx_api.requests, "patch") as patch:
        patch.return_value.status_code = 200
        r = _web_client(app, "bob").patch(
            "/api/mediamtx/stream/update", json={"path": "live/VIDEO_1", "record": record}
        )
    assert r.status_code == 200, r.json
    urls = [c.args[0] for c in patch.call_args_list]
    assert urls[0].endswith("/v3/config/paths/patch/live/VIDEO_1")
    assert urls[1].endswith("/v3/config/paths/patch/live/VIDEO_1" + WEB_COPY)
    assert patch.call_args_list[1].kwargs["json"] == {"record": record}
    with app.app_context():
        assert json.loads(db.session.get(VideoStream, "live/VIDEO_1").mediamtx_settings)["record"] is record


def test_record_toggle_without_a_web_copy_patches_only_the_stream(app):
    with mock.patch.object(mediamtx_api.requests, "patch") as patch:
        patch.return_value.status_code = 200
        r = _web_client(app, "alice").patch(
            "/api/mediamtx/stream/update", json={"path": "alicecam", "record": True}
        )
    assert r.status_code == 200, r.json
    assert len(patch.call_args_list) == 1

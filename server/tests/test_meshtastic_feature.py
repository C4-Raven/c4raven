"""RAVEN_ENABLE_MESHTASTIC: one switch for the admin-only Meshtastic page, its API and the MQTT
bridge. The UI asks /api/features; /api/meshtastic/* answers 404 while it is off. When on, reading
channels is for administrators and read-only admins with the "meshtastic" area; changes are for
administrators only."""

import json

import pytest
from flask_security import hash_password

from raven.admin_view import ADMIN_AREAS
from raven.defaultconfig import DefaultConfig
from raven.extensions import db
from raven.models.Meshtastic import MeshtasticChannel

OFF_TEXT = "Meshtastic is turned off on this server"
JSON = {"Accept": "application/json"}


@pytest.fixture
def mesh_setting(app):
    original = app.config.get("RAVEN_ENABLE_MESHTASTIC")
    original_channels = app.config.get("RAVEN_MESHTASTIC_DOWNLINK_CHANNELS")
    yield app.config
    app.config["RAVEN_ENABLE_MESHTASTIC"] = original
    app.config["RAVEN_MESHTASTIC_DOWNLINK_CHANNELS"] = original_channels


@pytest.fixture
def users(app):
    with app.app_context():
        ds = app.security.datastore
        for role in ("administrator", "user", "admin_viewer"):
            ds.find_or_create_role(role)
        ds.create_user(
            username="root", password=hash_password("RootPass1!"), roles=["administrator"]
        )
        ds.create_user(username="plain", password=hash_password("PlainPass1!"), roles=["user"])
        ds.create_user(
            username="viewer",
            password=hash_password("ViewPass1!"),
            roles=["user", "admin_viewer"],
            admin_view_areas=json.dumps(["meshtastic"]),
        )
        ds.create_user(
            username="viewer2",
            password=hash_password("ViewPass2!"),
            roles=["user", "admin_viewer"],
            admin_view_areas=json.dumps(["users", "groups"]),
        )
        db.session.commit()


class Client:
    def __init__(self, client, token):
        self.client = client
        self.token = token

    def _headers(self):
        return {**JSON, "X-CSRFToken": self.token}

    def get(self, path):
        return self.client.get(path, headers=self._headers())

    def post(self, path, body):
        return self.client.post(path, json=body, headers=self._headers())

    def delete(self, path):
        return self.client.delete(path, headers=self._headers())


def _login(app, username, password):
    client = app.test_client()
    token = client.get("/api/login", json={}).json["response"]["csrf_token"]
    r = client.post(
        "/api/login",
        json={"username": username, "password": password},
        headers={"X-CSRFToken": token},
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    return Client(client, token)


CHANNEL = {
    "name": "TestMesh",
    "psk": "AQ==",
    "uplink_enabled": True,
    "downlink_enabled": True,
    "modem_preset": 0,
    "lora_region": 1,
    "lora_hop_limit": 3,
}


def _calls(c, url="https://meshtastic.org/e/#x"):
    """Every Meshtastic route, as (name, response)."""
    return [
        ("list", c.get("/api/meshtastic/channel")),
        ("create", c.post("/api/meshtastic/channel", CHANNEL)),
        ("delete", c.delete(f"/api/meshtastic/channel?url={url}")),
        ("psk", c.get("/api/meshtastic/generate_psk")),
    ]


def _channel_count(app):
    with app.app_context():
        return db.session.query(MeshtasticChannel).count()


def test_meshtastic_is_off_by_default():
    assert DefaultConfig.RAVEN_ENABLE_MESHTASTIC is False


def test_meshtastic_is_an_admin_area():
    assert "meshtastic" in ADMIN_AREAS


def test_features_reports_meshtastic(app, users, mesh_setting):
    root = _login(app, "root", "RootPass1!")
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = False
    features = root.get("/api/features").json
    assert features["meshtastic"] is False
    assert set(features) == {"map", "meshtastic"}
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = True
    assert root.get("/api/features").json["meshtastic"] is True


def test_off_every_route_is_404_and_does_nothing(app, users, mesh_setting):
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = False
    for username, password in (
        ("root", "RootPass1!"),
        ("viewer", "ViewPass1!"),
        ("plain", "PlainPass1!"),
    ):
        c = _login(app, username, password)
        for name, r in _calls(c):
            assert r.status_code == 404, (username, name, r.get_data(as_text=True))
            assert r.json["error"] == OFF_TEXT
    assert _channel_count(app) == 0
    assert "TestMesh" not in (app.config.get("RAVEN_MESHTASTIC_DOWNLINK_CHANNELS") or {})


def test_off_still_needs_sign_in(app, mesh_setting):
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = False
    assert app.test_client().get("/api/meshtastic/channel", headers=JSON).status_code == 401


def test_on_admin_can_create_list_and_delete(app, users, mesh_setting):
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = True
    root = _login(app, "root", "RootPass1!")

    psk = root.get("/api/meshtastic/generate_psk")
    assert psk.status_code == 200 and psk.json["psk"]

    created = root.post("/api/meshtastic/channel", CHANNEL)
    assert created.status_code == 200, created.get_data(as_text=True)
    url = created.json["url"]
    assert _channel_count(app) == 1

    listing = root.get("/api/meshtastic/channel")
    assert listing.status_code == 200
    assert "TestMesh" in listing.get_data(as_text=True)

    deleted = root.client.delete(
        "/api/meshtastic/channel", query_string={"url": url}, headers=root._headers()
    )
    assert deleted.status_code == 200, deleted.get_data(as_text=True)
    assert _channel_count(app) == 0


def _stored_channel(app, root):
    created = root.post("/api/meshtastic/channel", CHANNEL)
    assert created.status_code == 200, created.get_data(as_text=True)
    return created.json["url"]


def test_on_plain_user_gets_403_on_everything(app, users, mesh_setting):
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = True
    url = _stored_channel(app, _login(app, "root", "RootPass1!"))
    plain = _login(app, "plain", "PlainPass1!")
    for name, r in _calls(plain, url):
        assert r.status_code == 403, (name, r.get_data(as_text=True))
    # The delete that used to be open to any signed-in user
    r = plain.client.delete(
        "/api/meshtastic/channel", query_string={"url": url}, headers=plain._headers()
    )
    assert r.status_code == 403
    assert _channel_count(app) == 1


def test_on_viewer_with_area_can_read_but_not_change(app, users, mesh_setting):
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = True
    url = _stored_channel(app, _login(app, "root", "RootPass1!"))
    viewer = _login(app, "viewer", "ViewPass1!")

    listing = viewer.get("/api/meshtastic/channel")
    assert listing.status_code == 200
    assert "TestMesh" in listing.get_data(as_text=True)
    # The key is in psk and inside the share URL: a read-only admin sees neither
    channel = listing.json["results"][0]
    assert channel["psk"] is None and channel["url"] == ""
    assert url not in listing.get_data(as_text=True)
    root_listing = _login(app, "root", "RootPass1!").get("/api/meshtastic/channel").json["results"][0]
    assert root_listing["url"] == url

    assert viewer.post("/api/meshtastic/channel", {**CHANNEL, "name": "Other"}).status_code == 403
    r = viewer.client.delete(
        "/api/meshtastic/channel", query_string={"url": url}, headers=viewer._headers()
    )
    assert r.status_code == 403
    assert viewer.get("/api/meshtastic/generate_psk").status_code == 403
    assert _channel_count(app) == 1


def test_on_viewer_without_area_gets_403(app, users, mesh_setting):
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = True
    url = _stored_channel(app, _login(app, "root", "RootPass1!"))
    viewer2 = _login(app, "viewer2", "ViewPass2!")
    for name, r in _calls(viewer2, url):
        assert r.status_code == 403, (name, r.get_data(as_text=True))
    assert _channel_count(app) == 1


def test_admin_changes_are_audited(app, users, mesh_setting):
    # The test app is built in CLI mode, without the audit hooks create_app() adds for the server
    from raven.audit import init_audit
    from raven.models.AuditEvent import AuditEvent

    init_audit(app)
    mesh_setting["RAVEN_ENABLE_MESHTASTIC"] = True
    root = _login(app, "root", "RootPass1!")
    url = _stored_channel(app, root)
    r = root.client.delete(
        "/api/meshtastic/channel", query_string={"url": url}, headers=root._headers()
    )
    assert r.status_code == 200
    with app.app_context():
        summaries = [
            e.summary or ""
            for e in db.session.query(AuditEvent).filter_by(category="admin").all()
            if "root" in (e.username, e.actor)
        ]
    assert any(s.startswith("Created Meshtastic channel") for s in summaries), summaries
    assert any(s.startswith("Deleted Meshtastic channel") for s in summaries), summaries

"""
Federated devices are labelled with their Federation Hub partner's name (raven/fedhub_names.py):
a provenance id is the "<server id>-<suffix>" full id a hub stamps on what it forwards, and a
partner presents the same full id as its connection's remoteServerId.

Hub JSON shapes are copied from the live hub's read-only GETs (2026-11-11), with the CA uids
shortened.
"""

import time

import pytest
import requests

from raven import fedhub_names as fn
from raven.extensions import db
from raven.models.EUD import EUD

OWN_HUB = "d59107b0e46a4ff8aeee0f7eede1b4c0"
OWN_FULL = OWN_HUB + "-2sBrAWf8"
PARTNER_FULL = "622713fa68fa49d8a240dc3fe9d65545-ODmwo4kG"
OWN_CA = "CN=C4Raven-FedHub-CA,OU=C4Raven-FedHub,O=ZZ,L=YY,ST=XX,C=US-" + "fd" * 32
VPN_CA = "CN=C4Raven-FedHub-CA,OU=C4Raven-FedHub,O=ZZ,L=YY,ST=XX,C=US-" + "8c" * 32

KNOWN_CAS = [
    {"alias": "fd" * 32, "nickname": "Pubserver", "uid": OWN_CA, "hubGroup": True},
    {"alias": "8c" * 32, "nickname": "Brisbane HQ", "uid": VPN_CA, "hubGroup": False},
]
POLICY = {
    "name": "C4Raven",
    "policy": {
        "entities": [
            {"id": "own", "type": "FederationGroup", "displayName": "Pubserver", "name": OWN_CA,
             "config": {"enabled": True}},
            {"id": "vpn", "type": "FederationGroup", "displayName": "VPN", "name": VPN_CA,
             "config": {"enabled": True}},
        ],
        "rules": [],
        "groupSets": [],
    },
}
CONNECTIONS = [
    {
        "connectionId": "raven-federate-" + "41" * 32 + "-136224285512120",
        "remoteServerId": "",
        "groupIdentities": [OWN_CA],
    },
    {
        "connectionId": "pub_d87dd61f-0930-4ffe-8ae2-fa27f2c937d3-" + "c0" * 32 + "-" + PARTNER_FULL,
        "remoteConnectionType": "FEDERATION_HUB_CLIENT",
        "remoteServerId": PARTNER_FULL,
        "groupIdentities": [VPN_CA],
    },
]


class Response:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body
        self.content = b"x" if body is not None else b""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeHub:
    def __init__(self, connections=CONNECTIONS, down=False):
        self.connections = connections
        self.down = down
        self.calls = []

    def __call__(self, method, path, **kwargs):
        self.calls.append(path)
        if self.down:
            raise requests.ConnectionError("hub down")
        return {
            "/getBrokerConfig": Response(200, {"id": OWN_HUB, "v2Port": 9102}),
            "/getKnownCaGroups": Response(200, KNOWN_CAS),
            "/getActivePolicy": Response(200, POLICY),
            "/getActiveConnections": Response(200, self.connections),
        }[path]


@pytest.fixture(autouse=True)
def fresh_cache():
    fn.reset()
    yield
    fn.reset()


def test_names_from_hub_maps_partner_server_id_to_partner_name():
    own, names = fn.names_from_hub(FakeHub())
    assert own == OWN_HUB
    # The partner's name is its CA nickname, exactly as on the Federation Hub page; our own
    # bridge's connection (no remoteServerId, own CA) isn't a partner
    assert names == {PARTNER_FULL: "Brisbane HQ"}


def test_no_active_policy_means_no_partners_but_keeps_hub_id():
    hub = FakeHub()
    original = hub.__call__

    def call(method, path, **kwargs):
        if path == "/getActivePolicy":
            return Response(404, None)
        return original(method, path, **kwargs)

    assert fn.names_from_hub(call) == (OWN_HUB, {})


@pytest.mark.parametrize(
    "ids, expected",
    [
        ([OWN_FULL, PARTNER_FULL], PARTNER_FULL),  # the hub's provenance set put ours first
        ([PARTNER_FULL, OWN_FULL], PARTNER_FULL),
        ([OWN_FULL], OWN_FULL),
        ([], None),
    ],
)
def test_pick_provenance_skips_our_own_hub(ids, expected):
    assert fn.pick_provenance(ids, OWN_HUB) == expected


def test_pick_provenance_without_hub_id_keeps_first():
    assert fn.pick_provenance([OWN_FULL, PARTNER_FULL], None) == OWN_FULL


def test_resolver_names_partner_and_matches_across_hub_restarts():
    hub = FakeHub()
    resolve = fn.resolver(lambda: hub)
    assert resolve(PARTNER_FULL) == "Brisbane HQ"
    # A restarted partner hub keeps its id but picks a new random suffix
    assert resolve("622713fa68fa49d8a240dc3fe9d65545-NEWSUFFX") == "Brisbane HQ"
    assert resolve(OWN_FULL) is None
    assert resolve("unknown-federate") is None
    assert resolve(None) is None
    # One hub read for the whole request
    assert hub.calls.count("/getActiveConnections") == 1


def test_resolver_does_not_touch_hub_without_federated_rows():
    hub = FakeHub()
    resolve = fn.resolver(lambda: hub)
    assert resolve(None) is None and resolve("") is None
    assert hub.calls == []


def test_hub_outage_gives_no_names_and_is_not_retried_every_request():
    hub = FakeHub(down=True)
    assert fn.resolver(lambda: hub)(PARTNER_FULL) is None
    assert fn.resolver(lambda: hub)(PARTNER_FULL) is None
    assert hub.calls == ["/getBrokerConfig"]


def test_names_are_cached_and_remembered_after_partner_disconnects(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(fn.time, "monotonic", lambda: clock[0])
    hub = FakeHub()
    assert fn.resolver(lambda: hub)(PARTNER_FULL) == "Brisbane HQ"
    hub.connections = []
    clock[0] += fn.TTL_SECONDS - 1
    assert fn.resolver(lambda: hub)(PARTNER_FULL) == "Brisbane HQ"
    assert hub.calls.count("/getActiveConnections") == 1
    clock[0] += 2
    # Refreshed: the partner is gone from the hub, but its name is remembered
    assert fn.resolver(lambda: hub)(PARTNER_FULL) == "Brisbane HQ"
    assert hub.calls.count("/getActiveConnections") == 2
    # ...and an outage keeps what we knew
    hub.down = True
    clock[0] += fn.TTL_SECONDS
    assert fn.resolver(lambda: hub)(PARTNER_FULL) == "Brisbane HQ"


def test_fedhub_disabled_never_calls_hub(app, monkeypatch):
    calls = []
    monkeypatch.setattr(fn, "_default_request_factory", lambda: calls.append(1) or FakeHub())
    app.config["RAVEN_FEDHUB_ENABLE"] = False
    with app.app_context():
        assert fn.resolver()(PARTNER_FULL) is None
        assert fn.own_hub_id() is None
    assert calls == []


@pytest.fixture
def admin(app):
    from flask_security import hash_password

    with app.app_context():
        ds = app.security.datastore
        ds.find_or_create_role("administrator")
        ds.create_user(username="admin", password=hash_password("x"), roles=["administrator"])
        db.session.commit()
        uniquifier = ds.find_user(username="admin").fs_uniquifier
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()
    return client


def _add_euds(app):
    with app.app_context():
        db.session.add(EUD(uid="fed-1", callsign="BLUE FIRE", federated_from=PARTNER_FULL))
        db.session.add(EUD(uid="fed-2", callsign="RED", federated_from=OWN_FULL))
        db.session.add(EUD(uid="local-1", callsign="LOCAL"))
        db.session.commit()


def _rows(response):
    assert response.status_code == 200, response.data
    return {r["uid"]: r for r in response.json["results"]}


def test_eud_api_includes_partner_name(app, admin, monkeypatch):
    hub = FakeHub()
    monkeypatch.setattr(fn, "_default_request_factory", lambda: hub)
    app.config["RAVEN_FEDHUB_ENABLE"] = True
    app.config["RAVEN_FEDHUB_API_ADDRESS"] = "https://hub.example.org:9100/api"
    _add_euds(app)

    rows = _rows(admin.get("/api/eud?per_page=50"))
    assert rows["fed-1"]["federated_from"] == PARTNER_FULL
    assert rows["fed-1"]["federated_from_name"] == "Brisbane HQ"
    assert rows["fed-2"]["federated_from_name"] is None
    assert rows["local-1"]["federated_from_name"] is None
    assert hub.calls.count("/getActiveConnections") == 1

    euds = {e["uid"]: e for e in admin.get("/api/map_state").json["euds"]}
    assert euds["fed-1"]["federated_from_name"] == "Brisbane HQ"


def test_eud_api_survives_hub_outage(app, admin, monkeypatch):
    monkeypatch.setattr(fn, "_default_request_factory", lambda: FakeHub(down=True))
    app.config["RAVEN_FEDHUB_ENABLE"] = True
    app.config["RAVEN_FEDHUB_API_ADDRESS"] = "https://hub.example.org:9100/api"
    _add_euds(app)

    rows = _rows(admin.get("/api/eud?per_page=50"))
    assert rows["fed-1"]["federated_from"] == PARTNER_FULL
    assert rows["fed-1"]["federated_from_name"] is None


def test_eud_api_survives_resolver_bug(app, admin, monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(fn, "resolver", broken)
    _add_euds(app)
    rows = _rows(admin.get("/api/eud?per_page=50"))
    assert rows["fed-1"]["federated_from_name"] is None


def test_socket_emits_leave_name_out(app):
    _add_euds(app)
    with app.app_context():
        eud = db.session.get(EUD, db.session.query(EUD.id).filter_by(uid="fed-1").scalar())
        assert "federated_from_name" not in eud.to_json()


def test_daemon_records_partner_not_own_hub(monkeypatch):
    from raven.controllers import federation_hub_daemon as daemon
    from raven.proto.federation import fig_pb2

    class Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    client = daemon.FigFederateClient.__new__(daemon.FigFederateClient)
    client.context = Ctx()
    monkeypatch.setattr(daemon.fedhub_names, "own_hub_id", lambda: OWN_HUB)
    event = fig_pb2.FederatedEvent(
        federateProvenance=[
            fig_pb2.FederateProvenance(federationServerId=OWN_FULL),
            fig_pb2.FederateProvenance(federationServerId=PARTNER_FULL),
        ]
    )
    assert client._provenance_id(event) == PARTNER_FULL

    def down():
        raise RuntimeError("hub down")

    monkeypatch.setattr(daemon.fedhub_names, "own_hub_id", down)
    assert client._provenance_id(event) == OWN_FULL
    assert client._provenance_id(fig_pb2.FederatedEvent()) is None

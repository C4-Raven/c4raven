"""Federated devices show as live on the EUDs page and the web map.

A federated device never connects to this server's EudHandler, so cot_parser
keeps its EUD row live from its SA instead (CoTController.update_federated_eud).
"""

from bs4 import BeautifulSoup
from sqlalchemy import select

from raven.extensions import db, logger
from raven.models.EUD import EUD
from raven.models.Team import Team

PEER = "peer-server-id"
UID = "S-1-5-21-0000-1001"

SA = (
    '<event version="2.0" uid="{uid}" type="a-f-G-U-C-I" how="m-g" time="{t}" start="{t}" stale="{t}">'
    '<point lat="-33.9" lon="150.9" hae="0" ce="9999999.0" le="9999999.0"/>'
    '<detail><takv device="Rugged" os="Windows 11" platform="WinTAK-AUS" version="5.8.0.147"/>'
    '<contact callsign="BLUE FIRE" endpoint="*:-1:stcp"/><__group name="Cyan" role="Team Member"/></detail></event>'
)
MARKER = (
    '<event version="2.0" uid="marker-1" type="a-h-G" how="h-g-i-g-o" time="{t}" start="{t}" stale="{t}">'
    '<point lat="-33.9" lon="150.9" hae="0" ce="9999999.0" le="9999999.0"/>'
    '<detail><contact callsign="Hostile 1"/></detail></event>'
)
T = "2026-11-11T07:53:00.950Z"


class FakeSocketIO:
    def __init__(self):
        self.emitted = []

    def emit(self, event, data, namespace=None):
        self.emitted.append((event, data))


def event_of(xml):
    return BeautifulSoup(xml, "xml").find("event")


def controller(app):
    from raven.cot_parser.cot_parser import CoTController

    return CoTController(app.app_context(), logger, db, FakeSocketIO())


def add_eud(app, **values):
    with app.app_context():
        db.session.add(EUD(uid=UID, **values))
        db.session.commit()


def get_eud(app, uid=UID):
    with app.app_context():
        return db.session.execute(select(EUD).filter_by(uid=uid)).scalar_one()


def test_federated_sa_marks_eud_connected_and_announces_it(app):
    with app.app_context():
        db.session.add(Team(name="Cyan"))
        db.session.commit()
    add_eud(app, callsign="BLUE FIRE", federated_from=PEER)
    c = controller(app)

    c.update_federated_eud(event_of(SA.format(uid=UID, t=T)), UID, PEER)

    eud = get_eud(app)
    assert eud.last_status == "Connected"
    assert eud.last_event_time is not None
    assert (eud.platform, eud.version, eud.device) == ("WinTAK-AUS", "5.8.0.147", "Rugged")
    assert eud.team_id is not None and eud.team_role == "Team Member"
    assert [e for e, _ in c.socketio.emitted] == ["eud"]
    assert c.socketio.emitted[0][1]["federated_from"] == PEER

    # Later SA updates the row but doesn't re-announce an already-live device
    c.update_federated_eud(event_of(SA.format(uid=UID, t=T)), UID, PEER)
    assert len(c.socketio.emitted) == 1


def test_previously_local_eud_gets_federated_badge(app):
    # BLUE FIRE was once enrolled here, then moved to the peer server
    add_eud(app, callsign="BLUE FIRE", last_status="Disconnected")
    c = controller(app)

    c.update_federated_eud(event_of(SA.format(uid=UID, t=T)), UID, PEER)

    eud = get_eud(app)
    assert eud.federated_from == PEER and eud.last_status == "Connected"
    assert len(c.socketio.emitted) == 1


def test_live_local_connection_is_not_taken_over(app):
    add_eud(app, callsign="BLUE FIRE", last_status="Connected")
    c = controller(app)

    c.update_federated_eud(event_of(SA.format(uid=UID, t=T)), UID, PEER)

    assert get_eud(app).federated_from is None
    assert c.socketio.emitted == []


def test_shared_markers_and_unknown_uids_are_ignored(app):
    with app.app_context():
        db.session.add(EUD(uid="marker-1", federated_from=PEER))
        db.session.commit()
    c = controller(app)

    c.update_federated_eud(event_of(MARKER.format(t=T)), "marker-1", PEER)
    c.update_federated_eud(event_of(SA.format(uid="nobody", t=T)), "nobody", PEER)

    assert get_eud(app, "marker-1").last_status is None
    assert c.socketio.emitted == []


def test_cot_parser_and_bridge_are_wired_up():
    import inspect

    from raven.controllers import federation_hub_daemon
    from raven.cot_parser import cot_parser

    assert '"federated_from": provenance_id' in inspect.getsource(federation_hub_daemon)
    assert 'self.update_federated_eud(event, uid, body["federated_from"])' in inspect.getsource(cot_parser)


def _geo(uid, type_, other=""):
    from raven.proto.federation import fig_pb2

    return fig_pb2.GeoEvent(uid=uid, type=type_, other=other)


def test_only_device_sa_counts_as_a_federated_eud():
    from raven.controllers.federation_hub_daemon import _is_device_event

    assert _is_device_event(_geo(UID, "a-f-G-U-C-I", '<detail><takv platform="WinTAK"/><contact callsign="BLUE FIRE"/></detail>'))
    assert _is_device_event(_geo(UID, "t-x-d-d"))
    # A marker a federated user dropped has a contact callsign but no takv
    assert not _is_device_event(_geo("marker-1", "a-h-G", '<detail><contact callsign="V21"/><link uid="%s" relation="p-p"/></detail>' % UID))


def test_shared_marker_is_attributed_to_its_known_creator(app):
    from raven.controllers.federation_hub_daemon import FigFederateClient

    add_eud(app, callsign="BLUE FIRE", federated_from=PEER)
    daemon = object.__new__(FigFederateClient)
    daemon.context = app.app_context()
    marker = '<detail><contact callsign="V21"/><link uid="{}" relation="p-p"/></detail>'

    assert daemon._known_creator_uid(_geo("marker-1", "a-h-G", marker.format(UID))) == UID
    assert daemon._known_creator_uid(_geo("marker-2", "a-h-G", marker.format("unknown-device"))) is None
    assert daemon._known_creator_uid(_geo("marker-3", "a-h-G", "<detail/>")) is None


# --- A local device's own traffic coming back from the hub is not federated traffic ---


def _bridge(monkeypatch):
    """A FigFederateClient with no hub, broker or database: what it would publish to
    cot_parser lands in .published, what it would send to the hub in ._outbound_queue."""
    import queue
    import threading

    from raven.controllers import federation_hub_daemon as fhd

    bridge = object.__new__(fhd.FigFederateClient)
    bridge.self_uid = "raven-federate"
    bridge.group_name = "Fed"
    bridge.federate_user_id = 1
    bridge._injected = fhd._RoundTripLog()
    bridge._sent = fhd._RoundTripLog()
    bridge._recently_sent_dms = {}
    bridge._recently_sent_dms_lock = threading.Lock()
    bridge._known_remote_members = {}
    bridge._known_remote_members_lock = threading.Lock()
    bridge._outbound_queue = queue.Queue()
    bridge.published = []
    monkeypatch.setattr(bridge, "_provenance_id", lambda event: PEER, raising=False)
    monkeypatch.setattr(bridge, "_ensure_federated_eud", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(bridge, "_known_creator_uid", lambda geo: None, raising=False)
    monkeypatch.setattr(
        bridge,
        "_publish_to_cot_parser",
        lambda message, channel: bridge.published.append(message),
        raising=False,
    )

    class Context:
        class app:
            config = {"RAVEN_NODE_ID": "node"}

    bridge.context = Context()
    return bridge


def _sa_message(t):
    return {"uid": UID, "cot": SA.format(uid=UID, t=t)}


def _echo_of(bridge, n=0):
    """The nth event the bridge queued for the hub, as the hub hands it back."""
    from raven.proto.federation import fig_pb2

    events = list(bridge._outbound_queue.queue)
    return fig_pb2.FederatedEvent(event=events[n].event, federateGroups=["Fed"])


T_LATER = "2026-11-11T07:56:00.950Z"


def test_every_returning_copy_of_a_local_devices_sa_is_dropped(monkeypatch):
    # cot_parser publishes a device's SA once per group it is in, the bridge sends each copy
    # to the hub, and each one comes back -- from the hub itself or by way of the partner.
    bridge = _bridge(monkeypatch)
    for group in ("Fed", "Cyan", "Ops"):
        bridge._on_outbound_message(_sa_message(T), group)
    assert bridge._outbound_queue.qsize() == 3

    for n in range(3):
        bridge._handle_federated_event(_echo_of(bridge, n), None)

    assert bridge.published == []
    assert UID not in bridge._known_remote_members


def test_a_late_copy_of_a_local_devices_sa_is_still_dropped(monkeypatch):
    from raven.controllers import federation_hub_daemon as fhd

    bridge = _bridge(monkeypatch)
    bridge._on_outbound_message(_sa_message(T), "Fed")
    bridge._on_outbound_message(_sa_message(T_LATER), "Fed")
    clock = fhd.time.monotonic()
    monkeypatch.setattr(fhd.time, "monotonic", lambda: clock + 600)

    # The older of the two, ten minutes on (a partner's bridge catching up on a backlog)
    bridge._handle_federated_event(_echo_of(bridge, 0), None)

    assert bridge.published == []


def test_a_device_that_moved_to_the_partner_is_still_federated_in(monkeypatch):
    # Its SA was last sent from here at T; a newer one arriving from the hub is the device
    # itself, now connected to the partner.
    bridge = _bridge(monkeypatch)
    bridge._on_outbound_message(_sa_message(T), "Fed")
    remote = _bridge(monkeypatch)
    remote._on_outbound_message(_sa_message(T_LATER), "Fed")

    bridge._handle_federated_event(_echo_of(remote), None)

    assert len(bridge.published) == 1
    assert '"federated_from": "{}"'.format(PEER) in bridge.published[0]


def test_an_injected_event_is_not_handed_back_on_any_group(monkeypatch):
    # The partner's device: injected once, then published by cot_parser to each of its groups
    remote = _bridge(monkeypatch)
    remote._on_outbound_message(_sa_message(T), "Fed")
    bridge = _bridge(monkeypatch)
    bridge._handle_federated_event(_echo_of(remote), None)
    assert len(bridge.published) == 1

    for group in ("Fed", "Cyan", "Ops"):
        bridge._on_outbound_message(_sa_message(T), group)
    assert bridge._outbound_queue.qsize() == 0

    # ...but once that device connects here, its newer SA goes out as usual
    bridge._on_outbound_message(_sa_message(T_LATER), "Fed")
    assert bridge._outbound_queue.qsize() == 1


def test_a_marker_edited_by_a_peer_with_a_slower_clock_is_accepted(monkeypatch):
    # Not a device's own SA: another device may re-send it with an earlier time than ours
    bridge = _bridge(monkeypatch)
    bridge._on_outbound_message({"uid": UID, "cot": MARKER.format(t=T_LATER)}, "Fed")
    remote = _bridge(monkeypatch)
    remote._on_outbound_message({"uid": "peer-device", "cot": MARKER.format(t=T)}, "Fed")

    bridge._handle_federated_event(_echo_of(bridge), None)  # our own, back from the hub
    assert bridge.published == []
    bridge._handle_federated_event(_echo_of(remote), None)  # the peer's edit
    assert len(bridge.published) == 1


# --- A partner's "went offline" notice marks the device Disconnected straight away ---

# send_disconnect_cot's shape: a random event uid, the device named in <link relation="p-p">
TXDD = (
    '<event version="2.0" uid="0b6f-random" type="t-x-d-d" how="h-g-i-g-o" time="{t}" start="{t}" stale="{t}">'
    '<point lat="0" lon="0" hae="0" ce="9999999" le="9999999"/>'
    '<detail><link relation="p-p" uid="{uid}" type="a-f-G-U-C"/></detail></event>'
)


def test_partner_disconnect_notice_is_sent_as_the_devices(monkeypatch):
    import json

    remote = _bridge(monkeypatch)
    remote._on_outbound_message({"uid": UID, "cot": TXDD.format(uid=UID, t=T_LATER)}, "Fed")
    bridge = _bridge(monkeypatch)
    monkeypatch.setattr(
        bridge, "_known_creator_uid", lambda geo: UID if UID in geo.other else None, raising=False
    )

    bridge._handle_federated_event(_echo_of(remote), None)

    assert len(bridge.published) == 1
    message = json.loads(bridge.published[0])
    assert message["uid"] == UID
    assert message["federated_from"] == PEER


def test_federated_disconnect_marks_federated_eud_disconnected(app):
    add_eud(app, callsign="BLUE FIRE", federated_from=PEER, last_status="Connected")
    c = controller(app)

    c.mark_eud_offline(event_of(TXDD.format(uid=UID, t=T)), UID, PEER)

    assert get_eud(app).last_status == "Disconnected"
    assert [e for e, _ in c.socketio.emitted] == ["eud"]


def test_federated_disconnect_leaves_live_local_connection_alone(app):
    add_eud(app, callsign="BLUE FIRE", last_status="Connected")
    c = controller(app)

    c.mark_eud_offline(event_of(TXDD.format(uid=UID, t=T)), UID, PEER)

    assert get_eud(app).last_status == "Connected"
    assert c.socketio.emitted == []


def test_local_disconnect_still_marks_local_eud_disconnected(app):
    add_eud(app, callsign="BLUE FIRE", last_status="Connected")
    c = controller(app)

    c.mark_eud_offline(event_of(TXDD.format(uid=UID, t=T)), UID)

    assert get_eud(app).last_status == "Disconnected"


def test_partner_disconnect_notice_for_an_unknown_device_is_sent_as_the_server(monkeypatch):
    # Its random uid isn't an EUD here, so as the sender it failed the cot.sender_uid
    # foreign key and the event was rejected with an ERROR
    import json

    remote = _bridge(monkeypatch)
    remote._on_outbound_message({"uid": UID, "cot": TXDD.format(uid="never-seen", t=T_LATER)}, "Fed")
    bridge = _bridge(monkeypatch)

    bridge._handle_federated_event(_echo_of(remote), None)

    assert json.loads(bridge.published[0])["uid"] == "node"


def test_disconnect_of_an_unidentified_connection_sends_nothing(app):
    c = controller(app)

    class Channel:
        published = []

        def basic_publish(self, **kwargs):
            self.published.append(kwargs)

    c.rabbit_channel = Channel()
    c.send_disconnect_cot(None, None)
    assert Channel.published == []

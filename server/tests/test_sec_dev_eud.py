"""
Security fixes in the streaming handler (EudHandler):

* a logged-in user can't take over another user's device uid (or its callsign DM queue)
* handle() drops a connection that buffers too much without a complete event

Uses the minimal app and socket-less handler from tests/test_eud_stream_auth.py.
"""

from flask_security import hash_password

from raven.eud_handler import EudHandler as eud_module
from raven.eud_handler.EudHandler import EudHandler
from raven.extensions import db
from raven.models.AuditEvent import AuditEvent  # noqa: F401 (create_all needs the table)
from raven.models.EUD import EUD
from tests.test_eud_stream_auth import _auth, app  # noqa: F401 (app is a fixture)

T = "2026-11-15T10:00:00.000Z"


def _sa(uid, callsign="ALPHA", platform="ATAK-CIV"):
    return (
        f'<event version="2.0" uid="{uid}" type="a-f-G-U-C" how="m-g" time="{T}" start="{T}" '
        f'stale="{T}"><point lat="1" lon="2" hae="0" ce="9" le="9"/><detail>'
        f'<takv device="Pixel" os="34" platform="{platform}" version="5.6"/>'
        f'<contact callsign="{callsign}" endpoint="*:-1:stcp"/></detail></event>'
    )


class _Handler(EudHandler):
    """No socket, no RabbitMQ: records what would be published and closes."""

    is_ssl = True

    def __init__(self, app, common_name=None):
        self.app = app
        self.common_name = common_name
        self.is_authenticated = False
        self.user = None
        self.uid = None
        self.callsign = None
        self.closed = False
        self.published = []
        self.bound_queues = []
        self.group_memberships = []
        self.consumer_tags = []
        self.client_address = ("192.0.2.10", 40000)

    def close_connection(self):
        self.closed = True

    def publish_cot(self, event):
        if event:
            self.published.append(event.attrs.get("uid"))

    def note_rx(self):
        pass


def _connect(app, username):
    h = _Handler(app, common_name=username)
    h.handle_auth("")
    assert h.is_authenticated
    return h


def _add_user(app, username, active=True):
    with app.app_context():
        app.security.datastore.create_user(
            username=username, password=hash_password("pw"), roles=["user"], active=active
        )
        db.session.commit()


def _own(app, uid, username, callsign=None, status=None):
    with app.app_context():
        user = app.security.datastore.find_user(username=username)
        db.session.add(
            EUD(uid=uid, user_id=user.id if user else None, callsign=callsign, last_status=status)
        )
        db.session.commit()


def _owner(app, uid):
    with app.app_context():
        eud = db.session.query(EUD).filter_by(uid=uid).one()
        if eud.user_id is None:
            return None
        return db.session.get(app.security.datastore.user_model, eud.user_id).username


def _audits(app):
    from raven.models.AuditEvent import AuditEvent

    with app.app_context():
        return [(a.action, a.username) for a in db.session.query(AuditEvent).all()]


# ---------------------------------------------------------------- uid takeover


def test_other_user_cannot_take_over_a_device_uid(app):
    _add_user(app, "bob")
    _own(app, "ANDROID-alice", "alice", callsign="ALICE", status="Connected")

    bob = _connect(app, "bob")
    bob.handle_cot(_sa("ANDROID-alice", callsign="BOB"))

    assert bob.uid is None
    assert bob.published == []
    assert _owner(app, "ANDROID-alice") == "alice"
    assert "ANDROID-alice" in bob.refused_uids
    # Later events as that device are dropped too, and the refusal is audited once
    bob.handle_cot(_sa("ANDROID-alice", callsign="BOB"))
    assert bob.published == []
    assert _audits(app).count(("uid_takeover_refused", "bob")) == 1


def test_same_user_reconnecting_keeps_working(app):
    _own(app, "ANDROID-alice", "alice", callsign="ALICE")

    alice = _connect(app, "alice")
    alice.handle_cot(_sa("ANDROID-alice", callsign="ALICE"))

    assert alice.uid == "ANDROID-alice"
    assert alice.published == ["ANDROID-alice"]
    assert _owner(app, "ANDROID-alice") == "alice"
    assert not alice.refused_uids


def test_unassigned_device_can_be_claimed(app):
    _add_user(app, "bob")
    _own(app, "ANDROID-new", None)

    bob = _connect(app, "bob")
    bob.handle_cot(_sa("ANDROID-new", callsign="BOB"))

    assert bob.uid == "ANDROID-new"
    assert _owner(app, "ANDROID-new") == "bob"


def test_device_of_deactivated_or_federated_owner_can_be_claimed(app):
    # fed-<callsign> accounts are inactive: a device that moved here from a partner connects
    _add_user(app, "bob")
    _add_user(app, "fed-BLUE", active=False)
    _own(app, "ANDROID-moved", "fed-BLUE")

    bob = _connect(app, "bob")
    bob.handle_cot(_sa("ANDROID-moved", callsign="BOB"))

    assert bob.uid == "ANDROID-moved"
    assert _owner(app, "ANDROID-moved") == "bob"


def test_auth_uid_does_not_reassign_another_users_device(app):
    _own(app, "ANDROID-admin", "admin", status="Connected")
    h = _Handler(app, common_name="alice")
    h.handle_auth(_auth("alice", "alicepass", uid="ANDROID-admin"))

    assert h.is_authenticated and not h.closed
    assert _owner(app, "ANDROID-admin") == "admin"
    assert "ANDROID-admin" in h.refused_uids


def test_disconnected_device_passes_to_the_new_account(app):
    """WinTAK's uid is the Windows account SID: every TAK account signed in on one PC shares
    it. Switching accounts on that PC (the owner's device offline) is not a takeover."""
    sid = "S-1-5-21-274003097-2070289950-685400601-1001"
    _add_user(app, "test")
    _own(app, sid, "alice", callsign="BLUE FIRE", status="Disconnected")

    h = _connect(app, "test")
    h.handle_cot(_sa(sid, callsign="BLUE FIRE", platform="WinTAK-AUS"))

    assert h.uid == sid
    assert h.published == [sid]
    assert not h.refused_uids
    assert _owner(app, sid) == "test"
    assert _audits(app).count(("uid_handover", "test")) == 1


def test_auth_uid_of_disconnected_device_moves_it(app):
    _own(app, "ANDROID-admin", "admin", status="Disconnected")
    h = _Handler(app, common_name="alice")
    h.handle_auth(_auth("alice", "alicepass", uid="ANDROID-admin"))

    assert h.is_authenticated and not h.refused_uids
    assert _owner(app, "ANDROID-admin") == "alice"


def test_relayed_radio_of_offline_owner_keeps_its_owner(app):
    """A relay never takes a radio's registration, whether its owner is connected or not."""
    _add_user(app, "radio-gw")
    _own(app, "DMR-3001", "alice", status="Disconnected")
    gw = _connect(app, "radio-gw")
    gw.handle_cot(_sa("DMR-3001", callsign="RADIO 3001", platform="DMRCOT"))
    assert gw.published == ["DMR-3001"]
    assert _owner(app, "DMR-3001") == "alice"


def test_relayed_mesh_node_passes_but_keeps_its_owner(app):
    _add_user(app, "bob")
    _own(app, "!a1b2c3d4", "alice")

    bob = _connect(app, "bob")
    bob.handle_cot(_sa("!a1b2c3d4", callsign="MESH1", platform="Meshtastic"))

    assert bob.published == ["!a1b2c3d4"]
    assert _owner(app, "!a1b2c3d4") == "alice"


def test_dmr_radio_gateway_relays_every_radio_user(app):
    """A DMR radio gateway (dmrcot) connects as its own user and relays the positions of
    every radio on its network, some of them registered to other users. All must flow."""
    _add_user(app, "radio-gw")
    _own(app, "DMR-1001", "alice")

    gw = _connect(app, "radio-gw")
    gw.handle_cot(_sa("DMR-1001", callsign="RADIO 1001", platform="DMRCOT"))
    gw.handle_cot(_sa("DMR-1002", callsign="RADIO 1002", platform="DMRCOT"))
    gw.handle_cot(_sa("DMR-1001", callsign="RADIO 1001", platform="DMRCOT"))

    assert gw.published == ["DMR-1001", "DMR-1002", "DMR-1001"]
    assert not gw.refused_uids
    assert _owner(app, "DMR-1001") == "alice"


def test_radio_gateway_on_plain_tcp_relays_everything(app):
    """Radios on the plain 8088 port (no login) are not subject to the ownership check."""
    _own(app, "DMR-2001", "alice")
    h = _Handler(app)
    h.is_ssl = False
    h.handle_cot(_sa("DMR-2001", callsign="RADIO 2001", platform="DMRCOT"))
    h.handle_cot(_sa("DMR-2002", callsign="RADIO 2002", platform="DMRCOT"))
    assert h.published == ["DMR-2001", "DMR-2002"]
    assert not h.refused_uids


def test_callsign_of_another_users_device_is_flagged(app):
    _add_user(app, "bob")
    _own(app, "ANDROID-alice", "alice", callsign="ALICE", status="Connected")

    bob = _connect(app, "bob")
    bob.handle_cot(_sa("ANDROID-bob", callsign="ALICE"))

    assert bob.uid == "ANDROID-bob"
    assert bob.callsign_queue_refused

    alice_new_phone = _connect(app, "alice")
    alice_new_phone.handle_cot(_sa("ANDROID-alice-2", callsign="ALICE"))
    assert not alice_new_phone.callsign_queue_refused


def test_reused_callsign_of_a_disconnected_device_keeps_dms(app):
    """Shift callsigns (MEDIC1) are reused: a stale row of someone who went home must not cut
    the new holder off from direct messages."""
    _add_user(app, "bob")
    _own(app, "ANDROID-alice", "alice", callsign="MEDIC1", status="Disconnected")

    bob = _connect(app, "bob")
    bob.handle_cot(_sa("ANDROID-bob", callsign="MEDIC1"))

    assert bob.uid == "ANDROID-bob"
    assert not bob.callsign_queue_refused


def test_plain_tcp_without_user_is_unchanged(app):
    _own(app, "ANDROID-alice", "alice")
    h = _Handler(app)
    h.is_ssl = False
    h.handle_cot(_sa("ANDROID-alice"))
    assert h.uid == "ANDROID-alice"


# ---------------------------------------------------------------- streaming buffer cap


class _Socket:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, n):
        return self.chunks.pop(0) if self.chunks else b""


class _StreamHandler(EudHandler):
    def __init__(self, chunks):
        self.request = _Socket(chunks)
        self.shutdown = False
        self.uid = None
        self.common_name = None
        self.client_address = ("192.0.2.10", 40000)
        self.handled = []
        self.closed = False
        self.recv_calls = 0

    def handle_cot(self, cot):
        self.handled.append(cot)

    def handle_auth(self, auth):
        self.handled.append(auth)

    def close_connection(self):
        self.closed = True


def test_buffer_without_closing_tag_is_capped(monkeypatch):
    monkeypatch.setattr(eud_module, "MAX_PENDING_COT_CHARS", 1000)
    chunk = b"<event " + b"x" * 400
    more = [b"x" * 400] * 10 + [b"</event>"]
    h = _StreamHandler([chunk] + more)
    h.handle()
    assert h.closed
    assert h.handled == []
    # Stopped reading once over the cap, long before the closing tag
    assert h.request.chunks


def test_normal_events_still_parse():
    a = _sa("ANDROID-a")
    b = _sa("ANDROID-b")
    h = _StreamHandler([(a + b).encode()])
    h.handle()
    assert h.handled == [a, b]
    assert h.closed  # the socket ended


def test_event_split_across_reads_under_the_cap_is_buffered():
    a = _sa("ANDROID-a")
    h = _StreamHandler([a[:50].encode(), a[50:].encode()])
    h.handle()
    assert h.handled == [a]


# ---------------------------------------------------------------- max_children


def test_eud_server_child_cap(monkeypatch):
    from raven.eud_handler.EudServer import EudServer

    assert EudServer.max_children == 2000

    class _App:
        config = {"RAVEN_EUD_MAX_CONNECTIONS": 5000}

    monkeypatch.setattr("socketserver.ForkingTCPServer.__init__", lambda self, *a, **k: None)
    server = EudServer(("127.0.0.1", 0), None, None, _App())
    assert server.max_children == 5000


def test_refused_connection_cannot_send_chat_under_its_own_uid(app):
    # A chat has no <takv>/<contact>, so it used to go out with no sender and its own uid
    # (and failed the cot.sender_uid foreign key in cot_parser)
    _add_user(app, "bob")
    _own(app, "ANDROID-alice", "alice", callsign="ALICE", status="Connected")
    bob = _connect(app, "bob")
    bob.handle_cot(_sa("ANDROID-alice", callsign="BOB"))
    assert "ANDROID-alice" in bob.refused_uids

    chat = (
        f'<event version="2.0" uid="GeoChat.ANDROID-alice.All Chat Rooms.1" type="b-t-f" '
        f'how="h-g-i-g-o" time="{T}" start="{T}" stale="{T}"><point lat="0" lon="0" hae="0" '
        f'ce="9" le="9"/><detail><remarks>hi</remarks></detail></event>'
    )
    bob.handle_cot(chat)
    assert bob.published == []

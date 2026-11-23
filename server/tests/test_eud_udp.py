"""UDP (port 8087) datagrams are unauthenticated and their source address is spoofable: they are
published anonymously and never create or change an EUD row, and nothing is sent back."""

import json

from raven.eud_handler.EudHandler import EudHandler
from raven.extensions import db
from raven.models.AuditEvent import AuditEvent  # noqa: F401 (create_all needs the table)
from raven.models.EUD import EUD
from tests.test_eud_stream_auth import app  # noqa: F401 (app is a fixture)

T = "2026-11-15T10:00:00.000Z"


def _sa(uid, callsign, platform="ATAK-CIV", type_="a-f-G-U-C"):
    return (
        f'<event version="2.0" uid="{uid}" type="{type_}" how="m-g" time="{T}" start="{T}" '
        f'stale="{T}"><point lat="1" lon="2" hae="0" ce="9" le="9"/><detail>'
        f'<takv device="Spoof" os="1" platform="{platform}" version="9.9"/>'
        f'<contact callsign="{callsign}" phone="5550100" endpoint="*:-1:stcp"/>'
        f'<__group name="Red" role="Team Lead"/></detail></event>'
    )


class _Channel:
    is_open = True
    is_closing = False
    is_closed = False

    def __init__(self):
        self.published = []
        self.calls = []

    def basic_publish(self, exchange, routing_key, body, properties=None):
        self.published.append((exchange, json.loads(body)))

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)

        return record


class _UdpSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))

    def shutdown(self, how):
        raise AssertionError("the UDP socket is the server's")

    def close(self):
        raise AssertionError("the UDP socket is the server's")


def _udp_handler(app, datagram):
    handler = EudHandler.__new__(EudHandler)
    handler.app = app
    handler.sock = _UdpSocket()
    handler.request = (datagram.encode(), handler.sock)
    handler.client_address = ("198.51.100.66", 4242)
    handler.rabbit_channel = _Channel()
    return handler


def _row(app, uid):
    with app.app_context():
        eud = db.session.query(EUD).filter_by(uid=uid).one_or_none()
        if eud is None:
            return None
        return {c.name: getattr(eud, c.name) for c in EUD.__table__.columns if c.name not in ("last_rx_at",)}


def test_udp_contact_for_an_owned_uid_leaves_the_eud_row_unchanged(app):
    with app.app_context():
        alice = app.security.datastore.find_user(username="alice")
        db.session.add(
            EUD(
                uid="ANDROID-alice",
                user_id=alice.id,
                callsign="ALICE",
                device="Pixel",
                os="34",
                platform="ATAK-CIV",
                version="5.6",
                last_status="Connected",
            )
        )
        db.session.commit()
    before = _row(app, "ANDROID-alice")

    handler = _udp_handler(app, _sa("ANDROID-alice", "MALLORY"))
    handler.handle()
    handler.close_connection()

    assert _row(app, "ANDROID-alice") == before
    assert handler.uid is None and handler.user is None
    # Still routed, but anonymously
    parsed = [body for exchange, body in handler.rabbit_channel.published if exchange == "cot_parser"]
    assert len(parsed) == 1
    assert parsed[0]["uid"] is None and parsed[0]["user_id"] is None
    assert "ANDROID-alice" in parsed[0]["cot"]
    # No queues, no disconnect message, nothing written back
    assert "queue_declare" not in handler.rabbit_channel.calls
    assert "basic_consume" not in handler.rabbit_channel.calls
    assert not any(body.get("disconnected") for _, body in handler.rabbit_channel.published)
    assert handler.sock.sent == []


def test_udp_contact_never_creates_an_eud_row(app):
    handler = _udp_handler(app, _sa("ANDROID-new", "NEW"))
    handler.handle()
    handler.close_connection()
    assert _row(app, "ANDROID-new") is None
    with app.app_context():
        assert db.session.query(EUD).count() == 0


def test_udp_ping_gets_no_reply(app):
    handler = _udp_handler(app, _sa("ANDROID-x-ping", "X", type_="t-x-c-t"))
    handler.handle()
    assert handler.sock.sent == []
    assert handler.rabbit_channel.published == []

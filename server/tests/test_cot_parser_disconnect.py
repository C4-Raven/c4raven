"""cot_parser's handling of an EudHandler "connection closed" notice (send_disconnect_cot).

The notice can sit in the cot_parser queue behind a backlog. If the device reconnects in the
meantime, its new session has already bound its queue to its groups: the old notice must not
unbind them (the device would silently stop receiving SA and chat) or mark it Disconnected.
"""

import datetime
import json

from sqlalchemy import select

# Imported at collection time, as the service imports it: its module-level app must be built
# from the session's test data folder, not from the first app fixture's (see
# test_test_logging_isolation.py)
from raven.cot_parser.cot_parser import CoTController, _notice_time
from raven.extensions import db, logger
from raven.models.EUD import EUD
from raven.models.Group import Group
from raven.models.GroupUser import GroupUser
from raven.models.user import User

UID = "ANDROID-reconnect"
UTC = datetime.timezone.utc


class FakeChannel:
    def __init__(self, log):
        self.log = log
        self.is_open = True
        self.is_closing = False

    def basic_publish(self, exchange, routing_key, body, properties=None):
        self.log.append(("publish", exchange, routing_key))

    def queue_declare(self, queue, passive=False):
        self.log.append(("declare", queue))

    def queue_unbind(self, queue, exchange, routing_key):
        self.log.append(("unbind", queue, exchange, routing_key))

    def close(self):
        self.is_open = False

    def basic_ack(self, delivery_tag):
        self.log.append(("ack",))


class FakeConnection:
    def __init__(self, log):
        self.log = log
        self.is_open = True

    def channel(self):
        return FakeChannel(self.log)


class Delivery:
    delivery_tag = 1


def naive_utc(**delta):
    return datetime.datetime.now(UTC).replace(tzinfo=None) + datetime.timedelta(**delta)


def controller(app):
    c = CoTController(app.app_context(), logger, db, None)
    c.log = []
    c.rabbit_channel = FakeChannel(c.log)
    c.rabbit_connection = FakeConnection(c.log)
    return c


def add_eud(app, **values):
    with app.app_context():
        db.session.add(EUD(uid=UID, callsign="RECONNECT", **values))
        db.session.commit()


def status(app):
    with app.app_context():
        return db.session.execute(select(EUD.last_status).where(EUD.uid == UID)).scalar_one()


def unbinds(c):
    return [entry for entry in c.log if entry[0] == "unbind"]


def test_disconnect_unbinds_and_marks_disconnected(app):
    add_eud(app, last_status="Disconnected", last_rx_at=naive_utc(minutes=-5))
    c = controller(app)

    c.send_disconnect_cot(UID, None, notice_time=naive_utc(seconds=-1))

    assert ("publish", "groups", "__ANON__.OUT") in c.log
    assert unbinds(c) == [("unbind", UID, "groups", "__ANON__.OUT")]
    assert status(app) == "Disconnected"


def test_disconnect_unbinds_the_users_out_groups(app):
    with app.app_context():
        user = User(username="reconnect-user", email="reconnect@example.com", password="x", active=True)
        user.fs_uniquifier = "reconnect-user"
        group = Group()
        group.name = "Cyan"
        group.type = "SYSTEM"
        group.bitpos = 3
        db.session.add_all([user, group])
        db.session.commit()
        db.session.add(GroupUser(user_id=user.id, group_id=group.id, direction="OUT", enabled=True))
        db.session.commit()
        user_id = user.id
    add_eud(app, last_status="Disconnected")
    c = controller(app)

    c.send_disconnect_cot(UID, user_id)

    assert ("publish", "groups", "Cyan.OUT") in c.log
    assert unbinds(c) == [("unbind", UID, "groups", "Cyan.OUT")]


def test_late_notice_leaves_a_reconnected_session_alone(app):
    # The device reconnected (its new EudHandler marked it Connected and bound its queue)
    # before cot_parser got to the old connection's notice
    add_eud(app, last_status="Connected", last_rx_at=naive_utc())
    c = controller(app)

    c.on_message(
        c.rabbit_channel,
        Delivery,
        None,
        json.dumps({"uid": UID, "cot": None, "disconnected": True, "user_id": None}),
    )

    assert unbinds(c) == []
    assert status(app) == "Connected"
    assert ("ack",) in c.log


def test_notice_older_than_the_devices_last_traffic_unbinds_nothing(app):
    # Not (yet) Connected again, but heard from after the notice was sent
    add_eud(app, last_status="Disconnected", last_rx_at=naive_utc(seconds=-1))
    c = controller(app)

    c.send_disconnect_cot(UID, None, notice_time=naive_utc(seconds=-30))

    assert unbinds(c) == []
    assert status(app) == "Disconnected"


def test_notice_time_comes_from_the_body_or_amqp_timestamp():
    class Properties:
        timestamp = 1700000000

    assert _notice_time({"time": "2026-11-11T07:53:00.950Z"}, None) == datetime.datetime(2026, 11, 11, 7, 53, 0, 950000)
    assert _notice_time({}, Properties) == datetime.datetime(2023, 11, 14, 22, 13, 20)
    assert _notice_time({}, None) is None

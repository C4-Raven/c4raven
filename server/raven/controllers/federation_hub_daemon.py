"""Standalone Federation Hub bridge daemon.

Bridges Raven's local CoT bus (RabbitMQ `groups` exchange) to Federation
Hub's FIG protocol (gRPC over mTLS on the broker's v2 port), so members of
RAVEN_FEDHUB_FEDERATE_GROUP are visible across whatever Federation Hub
already has federated. Federation Hub only relays what a federate actually
hands it -- this is the piece that does that handing-off.

Deliberately its OWN systemd service / OS process (like cot_parser and
eud_handler already are), never imported by or run inside the main `raven`
process. raven.app's gevent.monkey.patch_all() makes ordinary
threading.Thread a cooperative greenlet sharing one real OS thread with the
whole web server, and this bridge's workload -- multi-frame pika blocking
reads, a Queue handed between threads, a subprocess spawned to work around
the pika issue -- turned out to be unreliable inside that patched process no
matter how it was wired: a straight in-process consumer hung forever on any
message that needed multi-frame delivery; moving it to gevent's real-thread
threadpool fixed the hang but broke Queue and logging's internal locks
crossing the greenlet/thread boundary; spawning a dedicated unpatched
subprocess worker for just the RabbitMQ leg worked in isolation but gevent's
patched subprocess.Popen (and, after restoring the real Popen class, the
module-level _fork_exec global it also blanks out) turned even spawning that
subprocess into its own fight, and after finally winning that fight too, the
retry loop wrapping it silently stopped respawning after the first worker
died -- never proven root-caused, and not worth chasing further inside a
process this hostile to the workload. None of this is a problem here:
nothing else competes for this process's one thread of control, so
threading.Queue and pika.BlockingConnection.start_consuming() just work, the
same way they did in every standalone diagnostic script used to debug this
all along.
"""

import logging
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from xml.etree.ElementTree import Element, SubElement, tostring

import grpc
import json
import pika
import yaml
from bs4 import BeautifulSoup
from flask import Flask

from raven.defaultconfig import DefaultConfig
from raven.extensions import db, logger
from raven import fedhub_names
from raven.functions import datetime_from_iso8601_string, iso8601_string_from_datetime
from raven.proto.federation import fig_pb2, fig_pb2_grpc

# SQLAlchemy resolves string-based relationships (e.g. EUD's
# secondary="groups_missions") against every mapped class in the registry,
# not just the ones a given query touches -- the whole model graph has to be
# imported before the first query runs, the same as cot_parser.py does.
# User and Role are deliberately NOT imported here: they mix in
# Flask-Security's FsUserMixin, which needs fsqla.FsModels.set_db_info(db)
# called first (see create_app()) or class definition itself blows up.
from raven.models.Alert import Alert
from raven.models.CasEvac import CasEvac
from raven.models.Certificate import Certificate
from raven.models.Chatrooms import Chatroom
from raven.models.ChatroomsUids import ChatroomsUids
from raven.models.CITrap import CITrap
from raven.models.CoT import CoT
from raven.models.DataPackage import DataPackage
from raven.models.DeviceProfiles import DeviceProfiles
from raven.models.EUD import EUD
from raven.models.EUDStats import EUDStats
from raven.models.GeoChat import GeoChat
from raven.models.Group import Group
from raven.models.GroupMission import GroupMission
from raven.models.GroupUser import GroupUser
from raven.models.Icon import Icon
from raven.models.Marker import Marker
from raven.models.Meshtastic import MeshtasticChannel
from raven.models.Mission import Mission
from raven.models.MissionChange import MissionChange
from raven.models.MissionContentMission import MissionContentMission
from raven.models.MissionInvitation import MissionInvitation
from raven.models.MissionLogEntry import MissionLogEntry
from raven.models.MissionUID import MissionUID
from raven.models.Point import Point
from raven.models.RBLine import RBLine
from raven.models.Team import Team
from raven.models.VideoRecording import VideoRecording
from raven.models.VideoStream import VideoStream
from raven.models.WebAuthn import WebAuthn
from raven.models.ZMIST import ZMIST

# How long a direct message this bridge sent is remembered, to drop the hub's
# echo of it. It only needs to survive one round trip through the hub, so a
# few seconds of slack is generous.
_LOOPBACK_GUARD_SECONDS = 10

# How long _RoundTripLog remembers the last event it saw for a uid, and how far
# apart two timestamps of "the same event" may be: a CoT time survives the trip
# through the hub as whole milliseconds, give or take a rounding step.
_ROUND_TRIP_RETENTION_SECONDS = 3600
_ROUND_TRIP_TOLERANCE_MS = 5
_ROUND_TRIP_PRUNE_ABOVE = 4096

# The watchdog (FigFederateClient._watchdog_loop) asks the hub this often whether it
# still lists this bridge's connection, and reconnects after this many misses in a row.
WATCHDOG_INTERVAL_SECONDS = 60

# Inbound federateGroups (see _allowed_target_groups): how long the set of local group
# names is cached, and how often the same partner/group refusal is logged
_LOCAL_GROUPS_CACHE_SECONDS = 30
_REJECTED_GROUP_LOG_SECONDS = 600


def is_local_only_group(name: str) -> bool:
    """Groups a federated partner may never route into: __ANON__ (every local user's default)
    and the "Who Can See Whom" pairwise links ("Link: a and b", legacy "__uv__a__b"), which
    connect two local users and nobody else."""
    from raven.group_policy import LEGACY_PAIRWISE_PREFIX, PAIRWISE_PREFIX

    return (
        not isinstance(name, str)
        or not name
        or name == "__ANON__"
        or name.startswith(PAIRWISE_PREFIX)
        or name.startswith(LEGACY_PAIRWISE_PREFIX)
    )


WATCHDOG_MISSES_BEFORE_RECONNECT = 2


def _millis_from_iso8601(value: str | None) -> int:
    if not value:
        return int(datetime.now(timezone.utc).timestamp() * 1000)
    return int(datetime_from_iso8601_string(value).timestamp() * 1000)


def _iso8601_from_millis(value: int) -> str:
    if not value:
        return iso8601_string_from_datetime(datetime.now(timezone.utc))
    return iso8601_string_from_datetime(datetime.fromtimestamp(value / 1000, tz=timezone.utc))


def cot_xml_to_geoevent(cot_xml: str) -> fig_pb2.GeoEvent | None:
    """Flatten a Raven-internal CoT <event/> into the wire shape Federation
    Hub's FIG protocol expects. The full <detail> block is carried verbatim
    in `other` so a Raven peer on the other end can reconstruct the event
    with zero fidelity loss (see geoevent_to_cot_xml) -- the individual
    contact/__group/status/track fields below are populated too, but only
    as a best-effort fallback for a genuine TAK Server federate that doesn't
    know to look at `other`.
    """
    soup = BeautifulSoup(cot_xml, "xml")
    event = soup.find("event")
    if not event:
        return None

    point = event.find("point")
    detail = event.find("detail")
    contact = detail.find("contact") if detail else None
    group = detail.find("__group") if detail else None
    status = detail.find("status") if detail else None
    track = detail.find("track") if detail else None

    def f(tag, attr, default="0"):
        return float(tag[attr]) if tag and tag.has_attr(attr) else float(default)

    geo = fig_pb2.GeoEvent(
        sendTime=_millis_from_iso8601(event.get("time")),
        startTime=_millis_from_iso8601(event.get("start")),
        staleTime=_millis_from_iso8601(event.get("stale")),
        lat=f(point, "lat"),
        lon=f(point, "lon"),
        hae=f(point, "hae"),
        ce=f(point, "ce", "9999999"),
        le=f(point, "le", "9999999"),
        uid=event.get("uid", ""),
        type=event.get("type", ""),
        other=str(detail) if detail else "",
    )
    if contact and contact.has_attr("callsign"):
        geo.screenName = contact["callsign"]
    if contact and contact.has_attr("phone"):
        geo.phone = contact["phone"]
    if group and group.has_attr("name"):
        geo.groupName = group["name"]
    if group and group.has_attr("role"):
        geo.groupRole = group["role"]
    if status and status.has_attr("battery"):
        geo.battery = int(float(status["battery"]))
    if track and track.has_attr("course"):
        geo.course = float(track["course"])
    if track and track.has_attr("speed"):
        geo.speed = float(track["speed"])
    return geo


def _names_another_device(geo: fig_pb2.GeoEvent) -> bool:
    """Whether the event's <link relation="p-p"> names a uid other than the event's own."""
    if not geo.other:
        return False
    try:
        link = BeautifulSoup(geo.other, "xml").find("link", attrs={"relation": "p-p"})
    except Exception:
        return False
    return bool(link and link.get("uid") and link["uid"] != geo.uid)


def _is_device_event(geo: fig_pb2.GeoEvent) -> bool:
    """True for a TAK client's own SA (it carries <takv>) or its going-offline
    notice, as opposed to something the device shared (marker, shape, chat)."""
    return geo.type == "t-x-d-d" or "<takv" in (geo.other or "")


class _RoundTripLog:
    """The newest event per CoT uid that went one way through this bridge, to recognise it
    when it comes back the other way.

    Events come back: Federation Hub echoes a server's own traffic to it, a partner server
    can hand it back, and cot_parser publishes an event the bridge injected once per group
    it belongs to. Every one of those copies carries the CoT time of the original, so that
    is what identifies it, however many copies there are and however late they arrive.
    (Dropping only the first copy seen within a few seconds, as this used to, let the rest
    through: a local device's own position came back as the partner's, and the device was
    listed as federated from that partner the moment it disconnected here.)

    A device's own SA is only ever produced by that device, so anything for its uid that is
    not newer than the last one seen is the same event or a stale replay. Anything else
    (a marker, a shape) can be edited by another device whose clock runs behind, so only an
    identical time counts as the same event.
    """

    def __init__(self):
        self._entries = {}  # uid -> (monotonic time recorded, CoT time in ms)
        self._lock = threading.Lock()

    def remember(self, geo: fig_pb2.GeoEvent):
        now = time.monotonic()
        with self._lock:
            previous = self._entries.get(geo.uid)
            send_time = geo.sendTime
            if previous and _is_device_event(geo) and now - previous[0] < _ROUND_TRIP_RETENTION_SECONDS:
                send_time = max(send_time, previous[1])
            self._entries[geo.uid] = (now, send_time)
            if len(self._entries) > _ROUND_TRIP_PRUNE_ABOVE:
                for uid in [
                    uid
                    for uid, (at, _) in self._entries.items()
                    if now - at >= _ROUND_TRIP_RETENTION_SECONDS
                ]:
                    del self._entries[uid]

    def seen(self, geo: fig_pb2.GeoEvent) -> bool:
        with self._lock:
            entry = self._entries.get(geo.uid)
        if entry is None or time.monotonic() - entry[0] >= _ROUND_TRIP_RETENTION_SECONDS:
            return False
        if _is_device_event(geo):
            return geo.sendTime <= entry[1] + _ROUND_TRIP_TOLERANCE_MS
        return abs(geo.sendTime - entry[1]) <= _ROUND_TRIP_TOLERANCE_MS


def geoevent_to_cot_xml(geo: fig_pb2.GeoEvent) -> str:
    """Reverse of cot_xml_to_geoevent. `how` isn't part of the FIG wire
    format at all, so it can't be round-tripped -- default to machine/GPS,
    which is what every position-reporting EUD sends anyway.
    """
    event = Element(
        "event",
        {
            "version": "2.0",
            "uid": geo.uid,
            "type": geo.type,
            "how": "m-g",
            "time": _iso8601_from_millis(geo.sendTime),
            "start": _iso8601_from_millis(geo.startTime),
            "stale": _iso8601_from_millis(geo.staleTime),
        },
    )
    SubElement(
        event,
        "point",
        {
            "lat": str(geo.lat),
            "lon": str(geo.lon),
            "hae": str(geo.hae),
            "ce": str(geo.ce or 9999999),
            "le": str(geo.le or 9999999),
        },
    )

    detail_parsed = None
    if geo.other:
        try:
            detail_parsed = BeautifulSoup(geo.other, "xml").find("detail")
        except Exception:
            detail_parsed = None

    if detail_parsed:
        # Re-attach the peer's own <detail> verbatim under our fresh <event>.
        event.append(_bs4_to_etree(detail_parsed))
    else:
        detail = SubElement(event, "detail")
        if geo.screenName or geo.phone:
            attrs = {}
            if geo.screenName:
                attrs["callsign"] = geo.screenName
            if geo.phone:
                attrs["phone"] = geo.phone
            SubElement(detail, "contact", attrs)
        if geo.groupName or geo.groupRole:
            attrs = {}
            if geo.groupName:
                attrs["name"] = geo.groupName
            if geo.groupRole:
                attrs["role"] = geo.groupRole
            SubElement(detail, "__group", attrs)
        if geo.battery:
            SubElement(detail, "status", {"battery": str(geo.battery)})
        if geo.course or geo.speed:
            SubElement(detail, "track", {"course": str(geo.course), "speed": str(geo.speed)})

    return tostring(event).decode("utf-8")


def _bs4_to_etree(tag) -> Element:
    element = Element(tag.name, {k: str(v) for k, v in tag.attrs.items()})
    children = tag.find_all(recursive=False)
    if not children:
        text = tag.get_text()
        element.text = text if text else None
    for child in children:
        element.append(_bs4_to_etree(child))
    return element


# GeoEvent.other is a free-form string that both ends of this bridge control
# entirely, so it doubles as an envelope: a position/contact CoT stores just
# its <detail> block there (see cot_xml_to_geoevent), but a direct message
# needs the *whole* event forwarded verbatim (its <dest> tag lives outside
# <detail>) plus which specific recipient this delivery is for -- a single
# DM CoT can carry more than one <dest> (one cot_parser publish per
# recipient), so the routing key has to travel explicitly rather than be
# re-derived by re-parsing <dest> on the inbound side, or a multi-recipient
# DM's other remote recipients all collapse onto whichever <dest> happens to
# parse first. Format: "RAWDM:<routing_key>\x00<raw cot xml>".
_DM_MARKER = "RAWDM:"


class FigFederateClient:
    def __init__(self, context):
        self.context = context
        cfg = context.app.config
        self.group_name = cfg.get("RAVEN_FEDHUB_FEDERATE_GROUP")
        self.self_uid = cfg.get("RAVEN_FEDHUB_FEDERATE_UID")
        # What this bridge last injected from the hub, and what it last sent
        # to the hub, per CoT uid (see _RoundTripLog). _sent is how
        # _handle_federated_event tells "our own event, come back" from "a
        # genuinely remote federate whose uid happens to collide with a local
        # one" (a real case: the same physical device can be enrolled on two
        # servers, e.g. after a client reconnected to the wrong one -- its
        # newer events from the other server are real, current data, and
        # shouldn't be dropped just because a local EUD shares the uid).
        self._injected = _RoundTripLog()
        self._sent = _RoundTripLog()
        # uid -> monotonic time a direct message from it was last sent
        # outbound (see _on_dm_message), to drop the hub's echo of it.
        self._recently_sent_dms = {}
        self._recently_sent_dms_lock = threading.Lock()
        self._outbound_queue = queue.Queue()

        # uid -> callsign for every remote member we've heard from over the
        # gRPC inbound stream since this process started -- the roster the
        # DM bridge binds the `dms` exchange against (see
        # _dm_outbound_loop). Deliberately in-memory rather than the EUD
        # table's federated_from column: an EUD can predate this bridge
        # (already known locally, so never got that column set) and still
        # be a perfectly real remote federate once it starts sending events.
        self._known_remote_members = {}
        self._known_remote_members_lock = threading.Lock()

        # See _allowed_target_groups
        self._local_groups = None
        self._local_groups_at = 0.0
        self._rejected_group_logged = {}

        # Incoming federated CoT is attributed to this system account so it
        # flows through cot_parser's normal per-user group lookup (which is
        # what persists it, feeds the web map's socketio "point"/"eud"
        # events, and republishes it to the `groups` exchange) -- the same
        # path an EUD's own CoT takes, rather than only reaching TAK
        # protocol clients via a direct `groups` publish.
        with context:
            from raven.models.user import User

            federate_user = db.session.execute(
                db.session.query(User).filter_by(username=cfg.get("RAVEN_FEDHUB_FEDERATE_USERNAME"))
            ).first()
            self.federate_user_id = federate_user[0].id if federate_user else None
            if self.federate_user_id is None:
                logger.error(
                    "Federation Hub bridge: system account '{}' not found -- "
                    "inbound federated CoT won't be attributed to a group".format(
                        cfg.get("RAVEN_FEDHUB_FEDERATE_USERNAME")
                    )
                )

        self._cfg = cfg
        self._channel_lock = threading.Lock()
        self._stream_generation = 0
        self._channel = self._build_grpc_channel(cfg)
        self._stub = fig_pb2_grpc.FederatedChannelStub(self._channel)

    def start(self):
        threading.Thread(target=self._grpc_loop, name="fedhub-inbound", daemon=True).start()
        threading.Thread(target=self._server_event_loop, name="fedhub-server-event", daemon=True).start()
        threading.Thread(target=self._outbound_consumer_loop, name="fedhub-outbound", daemon=True).start()
        threading.Thread(target=self._dm_outbound_loop, name="fedhub-dm-outbound", daemon=True).start()
        if self._cfg.get("RAVEN_FEDHUB_API_ADDRESS"):
            threading.Thread(target=self._watchdog_loop, name="fedhub-watchdog", daemon=True).start()

    # -- watchdog: recover from a hub that silently forgot this bridge ------

    # The two streams only reconnect when gRPC reports an error. When the
    # hub restarts, or the bridge connects while the hub is still starting
    # (both happen when a server updates and restarts), the hub can end up
    # with no live registration for this bridge while the bridge's calls
    # stay open without error. Nothing flows either way and nothing is
    # logged, until someone restarts the bridge by hand. The hub's own
    # active-connections list is the ground truth, so check it.

    def _registered_with_hub(self, request) -> bool:
        r = request("GET", "/getActiveConnections")
        r.raise_for_status()
        prefix = f"{self.self_uid}-"
        return any(str(c.get("connectionId", "")).startswith(prefix) for c in (r.json() or []))

    def _watchdog_tick(self, request, misses: int) -> int:
        """One check. Returns the new count of misses in a row."""
        try:
            present = self._registered_with_hub(request)
        except Exception as e:  # noqa: BLE001 -- hub API down or not set up: can't tell
            logger.debug(f"Federation Hub bridge watchdog: couldn't ask the hub ({e})")
            return misses
        if present:
            return 0
        misses += 1
        if misses < WATCHDOG_MISSES_BEFORE_RECONNECT:
            return misses
        logger.warning(
            "Federation Hub bridge: the hub no longer lists this bridge's connection "
            "(it probably restarted) -- reconnecting"
        )
        self._reconnect()
        return 0

    def _watchdog_loop(self):
        from raven import fedhub_names

        request = fedhub_names.request_fn(self._cfg)
        misses = 0
        while True:
            time.sleep(WATCHDOG_INTERVAL_SECONDS)
            misses = self._watchdog_tick(request, misses)

    def _reconnect(self):
        """Swap in a fresh gRPC channel and close the old one. Closing cancels both
        streams; their loops reconnect on the new channel after their usual 5s."""
        with self._channel_lock:
            old = self._channel
            self._channel = self._build_grpc_channel(self._cfg)
            self._stub = fig_pb2_grpc.FederatedChannelStub(self._channel)
        try:
            old.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Federation Hub bridge: closing the old channel failed: {e}")

    def _build_grpc_channel(self, cfg) -> grpc.Channel:
        with open(cfg.get("RAVEN_FEDHUB_FEDERATE_CA"), "rb") as f:
            ca = f.read()
        with open(cfg.get("RAVEN_FEDHUB_FEDERATE_CERT"), "rb") as f:
            cert = f.read()
        with open(cfg.get("RAVEN_FEDHUB_FEDERATE_KEY"), "rb") as f:
            key = f.read()
        creds = grpc.ssl_channel_credentials(root_certificates=ca, private_key=key, certificate_chain=cert)
        # ServerEventStream is held open indefinitely (see _server_event_loop)
        # with nothing else on the channel to naturally surface a dead TCP
        # connection -- observed in production going silent for hours with
        # no error logged at all: items kept landing in _outbound_queue, but
        # nothing was reaching the hub, because the underlying connection had
        # died without grpc's channel state noticing. HTTP/2 keepalive pings
        # give it something to time out on, so a dead connection gets
        # detected and the channel reconnects instead of stalling forever.
        # A 30s ping interval was fine while the broker was also forcibly
        # dropping every client stream every 15s (clientTimeoutTime in its
        # own config) anyway -- but once that was raised, the broker's own
        # ping-flood protection started closing the connection itself
        # (GOAWAY: too_many_pings) roughly every 60s instead. 5 minutes is
        # comfortably past what a Federation Hub-scale broker tolerates
        # while still catching a genuinely dead connection well before it'd
        # go unnoticed for hours.
        options = [
            ("grpc.keepalive_time_ms", 300000),
            ("grpc.keepalive_timeout_ms", 20000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        return grpc.secure_channel(cfg.get("RAVEN_FEDHUB_BROKER_ADDRESS"), creds, options=options)

    # -- outbound: local group traffic -> Federation Hub --------------------

    def _outbound_consumer_loop(self):
        cfg = self.context.app.config
        while True:
            try:
                credentials = pika.PlainCredentials(
                    cfg.get("RAVEN_RABBITMQ_USERNAME"), cfg.get("RAVEN_RABBITMQ_PASSWORD")
                )
                connection = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        host=cfg.get("RAVEN_RABBITMQ_SERVER_ADDRESS"), credentials=credentials, heartbeat=0
                    )
                )
                channel = connection.channel()
                channel.queue_declare(queue="fedhub_federate", exclusive=True)
                # `groups` is a topic exchange (routing keys are
                # "<group name>.OUT"/".IN"), so a single "*.OUT" binding
                # dynamically covers every group's outbound traffic -- not
                # just RAVEN_FEDHUB_FEDERATE_GROUP -- with no per-group
                # bindings to add or refresh as groups come and go. Whatever
                # group a federated user is actually assigned to on this
                # server now gets bridged, not only the one fixed group.
                channel.queue_bind(exchange="groups", queue="fedhub_federate", routing_key="*.OUT")

                def on_message(ch, method, properties, body):
                    try:
                        message = json.loads(body)
                    except (TypeError, ValueError):
                        return
                    routing_key = method.routing_key or ""
                    if not routing_key.endswith(".OUT"):
                        return
                    group_name = routing_key[: -len(".OUT")]
                    # __ANON__ is every new user's automatic default (see
                    # _add_to_anon_group) -- federating it would silently
                    # send every local user's default traffic to every
                    # federated peer. Only groups an admin actually assigned
                    # someone to are meant to cross servers.
                    if group_name == "__ANON__":
                        return
                    self._on_outbound_message(message, group_name)

                channel.basic_consume(queue="fedhub_federate", on_message_callback=on_message, auto_ack=True)
                logger.info("Federation Hub bridge bound to all groups (except __ANON__)")
                channel.start_consuming()
            except BaseException:
                logger.error(traceback.format_exc())
            time.sleep(5)

    def _on_outbound_message(self, message: dict, group_name: str):
        uid = message.get("uid")
        if not uid or uid == self.self_uid:
            return

        geo = cot_xml_to_geoevent(message.get("cot", ""))
        if geo is None:
            logger.warning(f"FEDHUB outbound: cot_xml_to_geoevent returned None for uid={uid}")
            return
        if self._injected.seen(geo):
            # We injected this event from the hub -- don't hand it back, on
            # any of the groups cot_parser publishes it to.
            return
        self._sent.remember(geo)
        self._outbound_queue.put(fig_pb2.FederatedEvent(event=geo, federateGroups=[group_name]))
        logger.info(f"FEDHUB outbound: queued uid={uid} group='{group_name}'")

    # -- outbound: direct messages to a remote federated member -------------

    def _dm_outbound_loop(self):
        # Direct messages don't go through `groups` -- cot_parser publishes
        # them straight to the `dms` exchange (a `direct` exchange) with the
        # *recipient's* own uid/callsign as the routing key, which only ever
        # has a bound queue while that recipient is actually connected to
        # *this* server. A remote federated member is never connected here,
        # so with no matching binding the message is just dropped -- unless
        # we bind for them too. `direct` exchanges need one exact-match
        # binding per key (no wildcard), so this queue's bindings are kept
        # in sync with self._known_remote_members as it grows, which needs
        # start_consuming()'s blocking pump replaced with a manual one so
        # there's a place to periodically add new bindings.
        cfg = self.context.app.config
        while True:
            try:
                credentials = pika.PlainCredentials(
                    cfg.get("RAVEN_RABBITMQ_USERNAME"), cfg.get("RAVEN_RABBITMQ_PASSWORD")
                )
                connection = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        host=cfg.get("RAVEN_RABBITMQ_SERVER_ADDRESS"), credentials=credentials, heartbeat=0
                    )
                )
                channel = connection.channel()
                channel.queue_declare(queue="fedhub_dms", exclusive=True)

                def on_message(ch, method, properties, body):
                    try:
                        message = json.loads(body)
                    except (TypeError, ValueError):
                        return
                    self._on_dm_message(message, method.routing_key)

                channel.basic_consume(queue="fedhub_dms", on_message_callback=on_message, auto_ack=True)

                bound_keys = set()
                while True:
                    with self._known_remote_members_lock:
                        roster = dict(self._known_remote_members)
                    for uid, callsign in roster.items():
                        for key in (uid, callsign):
                            if key and key not in bound_keys:
                                channel.queue_bind(exchange="dms", queue="fedhub_dms", routing_key=key)
                                bound_keys.add(key)
                    connection.process_data_events(time_limit=2)
            except BaseException:
                logger.error(traceback.format_exc())
            time.sleep(5)

    def _on_dm_message(self, message: dict, routing_key: str):
        uid = message.get("uid")
        cot_xml = message.get("cot", "")
        if not uid or uid == self.self_uid or not cot_xml:
            return
        # routing_key is which of this CoT's (possibly several -- a single
        # DM CoT can carry multiple <dest> tags, one publish per recipient
        # from cot_parser's side) destinations *this* delivery is for.
        # Carrying it explicitly, rather than re-deriving it by re-parsing
        # <dest> out of cot_xml on the inbound side, is what keeps a
        # multi-recipient DM's other remote recipients from all being
        # collapsed onto whichever <dest> happens to parse first.
        with self._recently_sent_dms_lock:
            self._recently_sent_dms[uid] = time.monotonic()

        now_millis = int(time.time() * 1000)
        geo = fig_pb2.GeoEvent(
            uid=uid,
            sendTime=now_millis,
            startTime=now_millis,
            staleTime=now_millis,
            other=_DM_MARKER + routing_key + "\x00" + cot_xml,
        )
        self._outbound_queue.put(fig_pb2.FederatedEvent(event=geo, federateGroups=[self.group_name]))
        logger.info(f"FEDHUB outbound: queued DM from uid={uid} to '{routing_key}'")

    def _server_event_generator(self, generation=None):
        # One generator per ServerEventStream call. After a call ends (a
        # dropped stream, or the watchdog's reconnect), gRPC's request thread
        # can still be blocked in get() on the old generator and would swallow
        # the next event. So a generator from an older call stops, handing
        # any event it just took to the queue for the current call.
        while generation is None or generation == self._stream_generation:
            try:
                item = self._outbound_queue.get(timeout=1)
            except queue.Empty:
                continue
            if generation is not None and generation != self._stream_generation:
                self._outbound_queue.put(item)
                return
            yield item

    def _server_event_loop(self):
        # Federation Hub's own ServerEventStream handler (confirmed by
        # reading its source: tak.server.federation.hub.broker.
        # FederationHubBrokerService) sends its Subscription response and
        # then never closes the response stream, and separately its
        # SendOneEvent handler never responds at all -- so a Python client
        # waiting on either call to "complete" hangs until its own deadline
        # no matter what. What actually works: keep this call open
        # indefinitely with an infinite generator; the server processes each
        # event via onNext as it arrives regardless of whether the RPC
        # itself ever formally completes. Confirmed by broker byte counters
        # moving on a real event with the call still open.
        while True:
            try:
                self._stream_generation += 1
                self._stub.ServerEventStream(self._server_event_generator(self._stream_generation))
                logger.info("Federation Hub outbound stream ended -- reconnecting in 5s")
            except grpc.RpcError as e:
                logger.warning(
                    f"Federation Hub outbound stream dropped: {e.code()} {e.details()} -- reconnecting in 5s"
                )
            except BaseException:
                logger.error(traceback.format_exc())
            time.sleep(5)

    # -- inbound: Federation Hub -> local group ------------------------------

    def _grpc_loop(self):
        subscription = fig_pb2.Subscription(
            identity=fig_pb2.Identity(
                name=self.self_uid,
                uid=self.self_uid,
                description="C4Raven Federation Hub bridge",
            ),
            filter="",
        )
        inbound_conn = None
        inbound_channel = None
        while True:
            try:
                if inbound_conn is None or inbound_conn.is_closed:
                    inbound_conn = self._build_inbound_publish_connection()
                    inbound_channel = inbound_conn.channel()
                for federated_event in self._stub.ClientEventStream(subscription):
                    self._handle_federated_event(federated_event, inbound_channel)
            except grpc.RpcError as e:
                if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                    # The hub ends every stream after its clientTimeoutTime (an hour, set by
                    # c4raven-updater's fedhub.sh): routine, so reconnect at once and quietly
                    logger.info("Federation Hub event stream reached the hub's time limit -- reconnecting")
                    time.sleep(0.5)
                    continue
                logger.warning(
                    f"Federation Hub event stream dropped: {e.code()} {e.details()} -- reconnecting in 5s"
                )
            except BaseException:
                logger.error(traceback.format_exc())
            time.sleep(5)

    def _build_inbound_publish_connection(self):
        cfg = self.context.app.config
        credentials = pika.PlainCredentials(
            cfg.get("RAVEN_RABBITMQ_USERNAME"), cfg.get("RAVEN_RABBITMQ_PASSWORD")
        )
        return pika.BlockingConnection(
            pika.ConnectionParameters(
                host=cfg.get("RAVEN_RABBITMQ_SERVER_ADDRESS"),
                credentials=credentials,
                heartbeat=0,
            )
        )

    def _handle_federated_event(self, federated_event: fig_pb2.FederatedEvent, inbound_channel):
        if not federated_event.HasField("event"):
            return  # metadata-only frame (provenance/hop-limit announcements etc.)

        geo = federated_event.event
        if geo.uid == self.self_uid:
            return

        # Federation Hub echoes a server's own outbound traffic back to it
        # along with everyone else's, and a partner server can hand it back
        # too -- geo.uid == self.self_uid only catches the daemon's own
        # synthetic identity, not a real local device's uid. Without this, a
        # local device's own position/DM traffic gets treated as newly-arrived
        # federated traffic: the device is listed as federated from the
        # partner once it disconnects here, and it ends up in
        # _known_remote_members, which makes _dm_outbound_loop bind the local
        # device's own uid/callsign on the `dms` exchange, so a same-server DM
        # between two local users gets needlessly (and sometimes duplicately)
        # round-tripped through the hub.
        #
        # "Does a local EUD exist for this uid" is the wrong test: the same
        # physical device can be enrolled on two servers, and its newer
        # events from the other one are real. What we ourselves handed the
        # hub is the echo signal (see _RoundTripLog).
        if geo.other.startswith(_DM_MARKER):
            with self._recently_sent_dms_lock:
                sent_at = self._recently_sent_dms.pop(geo.uid, None)
            if sent_at is not None and (time.monotonic() - sent_at) < _LOOPBACK_GUARD_SECONDS:
                return
        elif self._sent.seen(geo):
            return

        with self._known_remote_members_lock:
            known = self._known_remote_members.get(geo.uid, "")
            resolved_callsign = geo.screenName or self._extract_callsign_from_other(geo.other) or known
            self._known_remote_members[geo.uid] = resolved_callsign

        if geo.other.startswith(_DM_MARKER):
            self._handle_federated_dm(geo, inbound_channel)
            return

        provenance_id = self._provenance_id(federated_event) or "unknown-federate"
        # Only a device's own SA (or its t-x-d-d) makes it an EUD. Markers,
        # shapes, casevacs and chat a federated user shares have their own
        # uids; registering those as EUDs put them on the EUDs page and the
        # map as users, each with its own fed-<callsign> account. Those go
        # in as sent by the device that made them (their <link
        # relation="p-p">) when it's a known EUD here, else as server-sent
        # (RAVEN_NODE_ID, which cot_parser stores with no sender).
        creator = self._known_creator_uid(geo) if geo.type == "t-x-d-d" else None
        if creator:
            # A server's "went offline" notice (send_disconnect_cot, TAK Server's too) has a
            # random uid of its own and names the device in its <link relation="p-p">. Send it
            # as that device's, so cot_parser marks the device Disconnected straight away
            # instead of it showing Connected until the 3-minute stale check.
            sender_uid = creator
        elif geo.type == "t-x-d-d" and _names_another_device(geo):
            # Such a notice about a device that isn't an EUD here: nothing to mark offline.
            # Sent as the server's (stored with no sender) so its random uid doesn't fail the
            # cot.sender_uid foreign key and get the event rejected.
            sender_uid = self.context.app.config.get("RAVEN_NODE_ID")
        elif _is_device_event(geo):
            self._ensure_federated_eud(geo, provenance_id, resolved_callsign)
            sender_uid = geo.uid
        else:
            sender_uid = self._known_creator_uid(geo) or self.context.app.config.get("RAVEN_NODE_ID")

        # federateGroups carries the sending server's own group name(s) for
        # this event (see _on_outbound_message) -- route it straight to the
        # same-named group(s) here rather than through federate_user_id's
        # (the "Server" account's) own memberships, so it reaches whichever
        # group it actually belongs to instead of only whatever "Server"
        # happens to be a member of. Falls back to the old user_id-based
        # routing if a peer doesn't set it (e.g. Fed group's own bootstrap
        # membership on "Server" still covers that case either way).
        #
        # The names come from the partner, so only groups that exist here and
        # aren't local-only (see _allowed_target_groups) are honoured. If the
        # event named groups and none of them qualify, target_groups_rejected
        # tells cot_parser not to fall back to the "Server" account's groups:
        # the event reaches no group, as it did before when the named groups
        # didn't exist here.
        cot_xml = geoevent_to_cot_xml(geo)
        named = [g for g in federated_event.federateGroups if g != "__ANON__"]
        target_groups = self._allowed_target_groups(named, provenance_id)
        message = json.dumps(
            {
                "uid": sender_uid,
                "cot": cot_xml,
                "user_id": self.federate_user_id,
                "target_groups": target_groups,
                "target_groups_rejected": bool(named) and not target_groups,
                # Tells cot_parser this came in over federation, so it can keep
                # the device's EUD row live (see update_federated_eud there).
                "federated_from": provenance_id,
            }
        )

        self._injected.remember(geo)

        self._publish_to_cot_parser(message, inbound_channel)

    def _local_group_names(self):
        """Names of the groups that exist on this server, cached for
        _LOCAL_GROUPS_CACHE_SECONDS. None if they couldn't be read (and never were)."""
        now = time.monotonic()
        cached = getattr(self, "_local_groups", None)
        if cached is not None and now - getattr(self, "_local_groups_at", 0.0) < _LOCAL_GROUPS_CACHE_SECONDS:
            return cached
        try:
            with self.context:
                names = set(db.session.execute(db.select(Group.name)).scalars().all())
            self._local_groups = names
            self._local_groups_at = now
            return names
        except Exception as e:  # noqa: BLE001 -- never let the lookup stop federation
            logger.error(f"FEDHUB inbound: couldn't read the local groups: {e}")
            return cached

    def _allowed_target_groups(self, names, provenance_id):
        """The federateGroups names a partner's event may be routed to here.

        federateGroups is set by the sending server, so a partner could otherwise name any
        group on this server, including the "Link: a and b" groups that connect exactly two
        local users, and have cot_parser publish into it. Which partner may share into which
        group is set on the Federation Hub page (each partner's receive filter, enforced by
        the hub before the event reaches this bridge); the bridge can't map an event back to
        a partner's policy node on its own, so here it enforces what holds for every
        partner: the group must exist on this server and must not be local-only
        (is_local_only_group). Refused names are logged at most every
        _REJECTED_GROUP_LOG_SECONDS per partner and group."""
        if not names:
            return []
        local = self._local_group_names()
        allowed, rejected = [], []
        for name in dict.fromkeys(names):
            if is_local_only_group(name) or (local is not None and name not in local):
                rejected.append(name)
            else:
                allowed.append(name)
        if rejected:
            now = time.monotonic()
            logged = getattr(self, "_rejected_group_logged", None)
            if logged is None:
                logged = self._rejected_group_logged = {}
            fresh = [
                n for n in rejected
                if now - logged.get((provenance_id, n), -_REJECTED_GROUP_LOG_SECONDS) >= _REJECTED_GROUP_LOG_SECONDS
            ]
            for n in fresh:
                logged[(provenance_id, n)] = now
            if len(logged) > 4096:
                logged.clear()
            if fresh:
                logger.warning(
                    "FEDHUB inbound: ignoring group(s) {} named by federate {}: not a group on "
                    "this server, or local-only".format(fresh, provenance_id)
                )
        return allowed

    def _provenance_id(self, federated_event):
        # The hub keeps provenance in a set, so [0] is arbitrary: with a partner hub in
        # the path it is often our own hub's id, which says nothing about where the
        # device is. Prefer the first id that isn't our hub's (see raven.fedhub_names),
        # which is the partner's own full id and names it on the EUDs page.
        ids = [p.federationServerId for p in federated_event.federateProvenance]
        if len(ids) < 2:
            return ids[0] if ids else None
        try:
            with self.context:
                own_hub_id = fedhub_names.own_hub_id()
        except Exception:  # noqa: BLE001 -- never let the lookup stop federation
            own_hub_id = None
        return fedhub_names.pick_provenance(ids, own_hub_id)

    def _handle_federated_dm(self, geo: fig_pb2.GeoEvent, inbound_channel):
        # Unlike a position/contact event, a direct message doesn't get
        # injected as a new local CoT -- it goes straight to the `dms`
        # exchange with the recipient's own uid/callsign as the routing
        # key, exactly like a same-server DM does, so it lands on their
        # real EudHandler connection instead of being (mis)treated as a
        # broadcast update from a device that doesn't exist here. Reuses
        # _grpc_loop's own inbound_channel rather than opening a fresh
        # RabbitMQ connection per message.
        routing_key, _, raw_xml = geo.other[len(_DM_MARKER) :].partition("\x00")
        if not routing_key or not raw_xml:
            logger.warning(f"FEDHUB inbound DM: malformed envelope for uid={geo.uid}, dropping")
            return
        message = json.dumps({"uid": geo.uid, "cot": raw_xml})
        inbound_channel.basic_publish(exchange="dms", routing_key=routing_key, body=message)
        logger.info(f"FEDHUB inbound: delivered DM from uid={geo.uid} to '{routing_key}'")

    def _known_creator_uid(self, geo: fig_pb2.GeoEvent) -> str | None:
        """The uid in the event's <link relation="p-p"> (the device that made
        it) if that device is an EUD here, else None."""
        if not geo.other:
            return None
        try:
            link = BeautifulSoup(geo.other, "xml").find("link", attrs={"relation": "p-p"})
        except Exception:
            return None
        if not link or not link.get("uid") or link["uid"] == geo.uid:
            return None
        with self.context:
            from raven.models.EUD import EUD

            found = db.session.execute(
                db.session.query(EUD.uid).filter_by(uid=link["uid"])
            ).first()
        return link["uid"] if found else None

    @staticmethod
    def _extract_callsign_from_other(other: str) -> str:
        # Not every inbound GeoEvent carries screenName -- a bare position
        # ping often doesn't re-announce <contact> at all -- but `other`
        # (the peer's raw <detail> block, round-tripped verbatim per
        # geoevent_to_cot_xml) sometimes still has it even then.
        if not other:
            return ""
        try:
            contact = BeautifulSoup(other, "xml").find("contact")
            if contact and contact.has_attr("callsign"):
                return contact["callsign"]
        except Exception:
            pass
        return ""

    def _ensure_federated_eud(self, geo: fig_pb2.GeoEvent, provenance_id: str, resolved_callsign: str = ""):
        # cot_parser's `cot` table has a foreign key requiring sender_uid to
        # already exist in `euds` -- a genuinely remote federated device was
        # never locally enrolled, so without this row every federated event
        # gets rejected with a ForeignKeyViolation before it ever reaches the
        # map or the groups exchange. federated_from marks it as not a real
        # local enrollment, for the UI to render distinctly.
        with self.context:
            from raven.models.EUD import EUD

            existing = db.session.execute(db.session.query(EUD).filter_by(uid=geo.uid)).first()
            if existing:
                existing = existing[0]
                changed = False
                # The very first event for a uid sometimes arrives with no
                # callsign anywhere on it -- a bare t-x-d-d "went offline"
                # notice, for instance, never carries one -- so it gets
                # created with the raw uid as a placeholder (see below) and
                # no user account at all (see below). Once a later event
                # supplies a real callsign, backfill it here.
                if resolved_callsign and existing.callsign == geo.uid:
                    existing.callsign = resolved_callsign
                    changed = True
                # Separately: a user account can be missing even when the
                # callsign is already fine -- _ensure_federated_user can
                # fail (a transient error, a name collision) independently
                # of the EUD row itself succeeding. Retry it here using
                # whatever callsign is on record, not just on the
                # placeholder-callsign path above, so it isn't stuck missing
                # forever just because the callsign was never the problem.
                if not existing.user_id and existing.callsign != geo.uid:
                    existing.user_id = self._ensure_federated_user(existing.callsign)
                    changed = True
                if changed:
                    try:
                        db.session.commit()
                    except BaseException:
                        db.session.rollback()
                    else:
                        self._rename_placeholder_federated_user(
                            existing.user_id, geo.uid, existing.callsign
                        )
                return

            # No callsign anywhere on this uid's first-ever event. When
            # that event is itself a t-x-d-d "went offline" notice, there's
            # no identity to remember and never will be -- skip creating a
            # row for it entirely instead of leaving a permanent
            # uid-as-callsign placeholder in the EUDs table forever.
            # cot_parser tolerates the missing row fine for this uid:
            # insert_cot swallows the resulting IntegrityError, and the
            # t-x-d-d handler just no-ops when it finds no existing EUD. (A server's notice
            # about another device is sent as the server's instead, see _handle_federated_event.)
            if not resolved_callsign and geo.type == "t-x-d-d":
                return

            # Still no callsign, but this is a real position/contact event
            # -- so register the EUD (cot_parser's FK still needs the row)
            # but skip creating a "fed-<uid>"-named user account for it. If
            # it turns out to be a real device after all, the backfill above
            # creates the account once we actually learn its callsign.
            callsign = resolved_callsign or geo.uid
            user_id = self._ensure_federated_user(resolved_callsign) if resolved_callsign else None
            eud = EUD(uid=geo.uid, callsign=callsign, federated_from=provenance_id, user_id=user_id)
            db.session.add(eud)
            try:
                db.session.commit()
            except BaseException:
                db.session.rollback()
                # Most likely the unique callsign collided with a local
                # device's -- still register the uid so cot_parser accepts
                # it, just without a friendly callsign.
                eud = EUD(uid=geo.uid, federated_from=provenance_id, user_id=user_id)
                db.session.add(eud)
                try:
                    db.session.commit()
                except BaseException:
                    db.session.rollback()
                    logger.error(traceback.format_exc())

    @staticmethod
    def _base_username_for_callsign(callsign: str) -> str:
        # callsign comes verbatim from a remote federate -- never trust it
        # into a username unsanitized.
        import bleach

        clean_callsign = bleach.clean(callsign, tags=[], strip=True).strip()[:64] if callsign else ""
        return f"fed-{clean_callsign}" if clean_callsign else "fed-user"

    def _ensure_federated_user(self, callsign: str) -> int | None:
        # A GroupUser row (what the Groups tab's "add member" actually
        # writes) is keyed by user_id, not by EUD -- so without a real User
        # row here, a federated device could never be added to any group
        # beyond the one it was federated in through. This account is never
        # meant to be logged into: active=False blocks it at the login form
        # regardless of the unusable random password, and it exists purely
        # so the device has a user_id to hang group memberships off of, the
        # same as any locally-enrolled EUD's owner does.
        import secrets

        from flask_security import hash_password

        from raven.models.user import User

        base_username = self._base_username_for_callsign(callsign)
        username = base_username
        suffix = 1
        while db.session.execute(db.session.query(User).filter_by(username=username)).first():
            suffix += 1
            username = f"{base_username}-{suffix}"

        try:
            user = self.context.app.security.datastore.create_user(
                username=username,
                password=hash_password(secrets.token_urlsafe(32)),
                active=False,
            )
            db.session.commit()
            return user.id
        except BaseException:
            db.session.rollback()
            logger.error(traceback.format_exc())
            return None

    def _rename_placeholder_federated_user(self, user_id: int | None, old_uid: str, callsign: str):
        # Mirrors the EUD.callsign backfill above: the account was created
        # with the raw uid as its username (via _ensure_federated_user, since
        # that's all _ensure_federated_eud had to give it at the time) --
        # once a real callsign is known, rename it to match so the Groups
        # member picker (and the Users page with "Show federated accounts"
        # on) doesn't show a wall of "fed-<uid>"-looking accounts forever.
        if not user_id:
            return
        with self.context:
            from raven.models.user import User

            user = db.session.execute(db.session.query(User).filter_by(id=user_id)).first()
            if not user:
                return
            user = user[0]

            # Only rename accounts still on their uid-derived placeholder --
            # never touch one that's since been given a real name some other
            # way (a prior heal, an admin edit).
            placeholder_base = self._base_username_for_callsign(old_uid)
            if user.username != placeholder_base and not user.username.startswith(f"{placeholder_base}-"):
                return

            new_base = self._base_username_for_callsign(callsign)
            new_username = new_base
            suffix = 1
            while (
                new_username != user.username
                and db.session.execute(
                    db.session.query(User).filter_by(username=new_username)
                ).first()
            ):
                suffix += 1
                new_username = f"{new_base}-{suffix}"

            if new_username == user.username:
                return

            user.username = new_username
            try:
                db.session.commit()
            except BaseException:
                db.session.rollback()
                logger.error(traceback.format_exc())

    def _publish_to_cot_parser(self, message: str, channel):
        # Feeding cot_parser (rather than publishing to `groups` ourselves)
        # gets federated CoT the same treatment as any EUD's own traffic:
        # persisted, fanned out to the web map's socketio events, and
        # published to `groups` under the federate account's own memberships.
        channel.basic_publish(exchange="cot_parser", routing_key="cot_parser", body=message)


_BRIDGE_LOG_LAYOUT = "%(asctime)s %(levelname)-8s fedhub_bridge pid=%(process)d %(module)s:%(lineno)d %(funcName)s | %(message)s"


def _bridge_log_handlers(app):
    """Console (only when attached to a terminal) plus the rotating fedhub_bridge.log."""
    log_dir = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "logs")
    os.makedirs(log_dir, exist_ok=True)
    rotating = TimedRotatingFileHandler(
        os.path.join(log_dir, "fedhub_bridge.log"),
        when=app.config.get("RAVEN_LOG_ROTATE_WHEN"),
        interval=app.config.get("RAVEN_LOG_ROTATE_INTERVAL"),
        backupCount=app.config.get("RAVEN_BACKUP_COUNT"),
    )
    rotating.setFormatter(logging.Formatter(_BRIDGE_LOG_LAYOUT))
    handlers = [rotating]

    if sys.stdout.isatty():
        import colorlog

        console = colorlog.StreamHandler()
        console.setFormatter(
            colorlog.ColoredFormatter("%(log_color)s" + _BRIDGE_LOG_LAYOUT, datefmt="%Y-%m-%d %H:%M:%S %Z")
        )
        handlers.insert(0, console)
    return handlers


def setup_logging(app):
    logger.setLevel(logging.DEBUG if app.config.get("DEBUG") else logging.INFO)
    for handler in _bridge_log_handlers(app):
        logger.addHandler(handler)


def create_app():
    app = Flask(__name__)
    app.config.from_object(DefaultConfig)

    config_path = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "config.yml")
    if os.path.exists(config_path):
        app.config.from_file(config_path, load=yaml.safe_load)

    setup_logging(app)
    db.init_app(app)

    # raven.models.user.User mixes in Flask-Security's FsUserMixin, which
    # resolves its own relationships against FsModels.db at class-definition
    # time -- has to happen before that module is ever imported (here, via
    # FigFederateClient's own `from raven.models.user import User`).
    import sqlalchemy.exc
    from flask_security.models import fsqla

    try:
        fsqla.FsModels.set_db_info(db)
    except sqlalchemy.exc.InvalidRequestError:
        pass

    from raven.models.role import Role
    from raven.models.user import User

    # Full Security() init (not just set_db_info) -- hash_password() (used
    # by _ensure_federated_user) reads its hashing scheme from config that
    # only this registers, and blows up with a bare datastore otherwise.
    # This daemon never serves HTTP, so the login views/blueprints it adds
    # just sit unused; mirrors raven.app's own Security() call so a
    # federated-placeholder account hashes exactly like a real one.
    import flask_wtf
    from flask_security import Security, SQLAlchemyUserDatastore

    from raven.forms.SiteAccessLoginForm import SiteAccessLoginForm
    from raven.models.WebAuthn import WebAuthn
    from raven.no_email import NoEmailMailUtil, apply_no_email_settings
    from raven.PasswordValidator import PasswordValidator
    from raven.UsernameValidator import UsernameValidator

    flask_wtf.CSRFProtect(app)
    user_datastore = SQLAlchemyUserDatastore(db, User, Role, WebAuthn)
    apply_no_email_settings(app.config)
    app.security = Security(
        app,
        user_datastore,
        mail_util_cls=NoEmailMailUtil,
        password_util_cls=PasswordValidator,
        username_util_cls=UsernameValidator,
        login_form=SiteAccessLoginForm,
    )

    # This process resolves every relationship on its own (Mission.contents -> "MissionContent",
    # ...), so every model must be imported, not just the ones named above
    from raven.models import import_all_models

    import_all_models()

    return app


def main():
    app = create_app()

    # Lets an admin restart the bridge from the Federation Hub page (see
    # raven/fedhub_bridge_control.py). Started even when federation is off,
    # so a restart there picks up federation once it's been enabled.
    from raven import fedhub_bridge_control

    threading.Thread(
        target=fedhub_bridge_control.run_control_loop,
        args=(app.config,),
        name="fedhub-control",
        daemon=True,
    ).start()

    from raven.fedhub_settings import bridge_configured

    if not bridge_configured(app.config):
        logger.info("Federation Hub bridge disabled (federation off or no hub address set up)")
        # Idle forever rather than exiting -- keeps the systemd unit in a
        # stable "running" state instead of flapping through restarts.
        while True:
            time.sleep(3600)

    client = FigFederateClient(app.app_context())
    client.start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()

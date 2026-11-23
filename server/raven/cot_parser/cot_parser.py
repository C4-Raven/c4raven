"""The CoT parser service.

Every CoT the server receives (EUD connections, the web API, server jobs, the federation bridge)
is published to the ``cot_parser`` queue. Each worker of this service takes one message at a
time, stores the event and everything derived from it (points, GeoChat, alerts, CASEVACs,
markers, R&B lines, stats, video feeds, Data Sync content, data package recipients), tells the
web UI about it over Socket.IO, optionally repeats positions and chat to Meshtastic, and finally
routes the event to its recipients through the ``groups``, ``dms`` and ``missions`` exchanges.

Run as the ``cot_parser`` console script (systemd unit cot_parser.service).
"""

import json
import logging
import os
import random
import re
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

import bleach
import pika
import sqlalchemy.exc
import unishox2
import yaml
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from flask_security import SQLAlchemyUserDatastore
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2
from sqlalchemy import select, update

from raven import metrics
from raven.config_file import write_default_config
from raven.cot_sharing import parse_fileshare  # noqa: F401  (re-exported, other modules import it from here)
from raven.db_upsert import insert_ignore
from raven.defaultconfig import DefaultConfig
from raven.extensions import db, logger
from raven.functions import (
    add_unknown_point,
    cot_type_to_2525c,
    datetime_from_iso8601_string,
    get_affiliation,
    get_battle_dimension,
    iso8601_string_from_datetime,
)
from raven.proto import atak_pb2

BROADCAST_NUM = 0xFFFFFFFF

LOG_FORMAT = (
    "[%(asctime)s] - cot_parser[%(process)d] - %(module)s - %(funcName)s - %(lineno)d - %(levelname)s - %(message)s"
)

SOCKETIO_NAMESPACE = "/socket.io"

# The flow tag TAK Server stamps on the CoT it generates itself
FLOW_TAG = "TAK-Server-f1a8159ef7804f7a8a32d8efc4b773d0"

MARKER_TYPE = re.compile(r"^a-[fhupansjk]-[ZPAGSUF]|^b-m-p")
RB_LINE_TYPE = re.compile(r"^u-rb")

# geochat.remarks is VARCHAR(255)
GEOCHAT_REMARKS_LENGTH = 255

EUD_TEXT_LENGTH = 255

STATS_INT_ATTRS = (
    "app_framerate",
    "deviceDataRx",
    "deviceDataTx",
    "heap_current_size",
    "heap_free_size",
    "heap_max_size",
    "storage_available",
    "storage_total",
    "battery_temp",
    "battery",
)

MARKER_UPDATE_FIELDS = (
    "uid",
    "affiliation",
    "battle_dimension",
    "callsign",
    "readiness",
    "argb",
    "color_hex",
    "iconset_path",
    "parent_callsign",
    "relation",
    "relation_type",
    "production_time",
    "location_source",
    "mil_std_2525c",
)

RB_LINE_UPDATE_FIELDS = (
    "uid",
    "timestamp",
    "range",
    "inclination",
    "anchor_uid",
    "range_units",
    "bearing_units",
    "north_ref",
    "color",
    "color_hex",
    "stroke_weight",
    "stroke_style",
    "labels_on",
    "end_latitude",
    "end_longitude",
)

# <detail> child tag -> rb_lines column, for the plain value="..." ones
RB_LINE_VALUES = {
    "range": ("range", float),
    "bearing": ("bearing", float),
    "anchorUID": ("anchor_uid", str),
    "rangeUnits": ("range_units", lambda value: int(float(value))),
    "bearingUnits": ("bearing_units", lambda value: int(float(value))),
    "northRef": ("north_ref", lambda value: int(float(value))),
    "strokeColor": ("stroke_color", lambda value: int(float(value))),
    "strokeWeight": ("stroke_weight", float),
}

child_processes = []


def is_local_only_group(name) -> bool:
    """Groups a federated partner may never route into: __ANON__ and the pairwise "Who Can See
    Whom" links ("Link: a and b", legacy "__uv__a__b"). Same rule as the federation bridge's."""
    from raven.group_policy import LEGACY_PAIRWISE_PREFIX, PAIRWISE_PREFIX

    return (
        not isinstance(name, str)
        or not name
        or name == "__ANON__"
        or name.startswith(PAIRWISE_PREFIX)
        or name.startswith(LEGACY_PAIRWISE_PREFIX)
    )


def _filter_target_groups(target_groups, target_groups_rejected):
    """The partner's groups this server may route into, and whether the event is rejected
    (the partner named groups, none of them usable here)."""
    if target_groups is None:
        return None, bool(target_groups_rejected)
    groups = [name for name in target_groups if not is_local_only_group(name)]
    rejected = bool(target_groups_rejected) or (bool(target_groups) and not groups)
    return groups, rejected


def _utc_naive(value):
    """A datetime for the tz-naive UTC DateTime columns."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _event_time(value):
    return _utc_naive(datetime_from_iso8601_string(value))


def _notice_time(body, properties):
    """When a connection-closed notice was sent: its "time" (ISO 8601 Z) if the body has one,
    else the AMQP timestamp property (Unix seconds), else None. Naive UTC."""
    try:
        if body.get("time"):
            return _event_time(body["time"])
        timestamp = getattr(properties, "timestamp", None)
        if timestamp:
            return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hex8(value) -> str:
    return format(int(value) & 0xFFFFFFFF, "08X")


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_attr(value):
    """An integer attribute of <stats>; tolerates a decimal point."""
    try:
        return int(value)
    except ValueError:
        return int(float(value))


def _strip_tags(value):
    return bleach.clean(value, tags=[], strip=True)[:EUD_TEXT_LENGTH]


def _scalar(value) -> float:
    """A float from pygc's results, which are one-element arrays."""
    item = getattr(value, "item", None)
    return float(item() if item is not None else value)


def _compress(text) -> bytes:
    return unishox2.compress(text or "")[0]


def _tak_group(team_name, role):
    group = atak_pb2.Group()
    try:
        group.team = atak_pb2.Team.Value((team_name or "Cyan").replace(" ", "_"))
    except ValueError:
        group.team = atak_pb2.Team.Value("Cyan")
    try:
        group.role = atak_pb2.MemberRole.Value((role or "TeamMember").replace(" ", ""))
    except ValueError:
        group.role = atak_pb2.MemberRole.Value("TeamMember")
    return group


def setup_logging(app):
    level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    logger.setLevel(level)

    if sys.stdout.isatty():
        import colorlog

        color_handler = colorlog.StreamHandler()
        color_handler.setFormatter(
            colorlog.ColoredFormatter("%(log_color)s" + LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S %Z")
        )
        logger.addHandler(color_handler)

    log_folder = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "logs")
    os.makedirs(log_folder, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        os.path.join(log_folder, "cot_parser.log"),
        when=app.config.get("RAVEN_LOG_ROTATE_WHEN", "midnight"),
        interval=app.config.get("RAVEN_LOG_ROTATE_INTERVAL", 0),
        backupCount=app.config.get("RAVEN_BACKUP_COUNT", 7),
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(file_handler)


def create_app():
    app = Flask(__name__)
    app.config.from_object(DefaultConfig)

    config_path = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "config.yml")
    if os.path.exists(config_path):
        app.config.from_file(config_path, load=yaml.safe_load)
    else:
        logger.info("Creating config.yml")
        write_default_config(config_path)

    setup_logging(app)
    db.init_app(app)

    from flask_security.models import fsqla_v3 as fsqla

    try:
        fsqla.FsModels.set_db_info(db)
    except sqlalchemy.exc.InvalidRequestError:
        pass

    from raven.models.role import Role
    from raven.models.user import User
    from raven.models.WebAuthn import WebAuthn

    SQLAlchemyUserDatastore(db, User, Role, WebAuthn)

    # This process resolves every relationship on its own, so all models must be imported
    from raven.models import import_all_models

    import_all_models()

    app.add_url_rule("/status", "status", status)
    return app


def status():
    return jsonify({"status": "ok"})


app = create_app()

# Imported after create_app(): the user models need Flask-Security's db info first
from raven import cot_sharing, offline_chat  # noqa: E402
from raven.models.Alert import Alert  # noqa: E402
from raven.models.CasEvac import CasEvac  # noqa: E402
from raven.models.Chatrooms import Chatroom  # noqa: E402
from raven.models.ChatroomsUids import ChatroomsUids  # noqa: E402
from raven.models.CoT import CoT  # noqa: E402
from raven.models.EUD import EUD  # noqa: E402
from raven.models.EUDStats import EUDStats  # noqa: E402
from raven.models.GeoChat import GeoChat  # noqa: E402
from raven.models.Group import Group  # noqa: E402
from raven.models.GroupUser import GroupUser  # noqa: E402
from raven.models.Icon import Icon  # noqa: E402
from raven.models.Marker import Marker  # noqa: E402
from raven.models.Meshtastic import MeshtasticChannel  # noqa: E402
from raven.models.Mission import Mission  # noqa: E402
from raven.models import MissionChange as mission_change_module  # noqa: E402
from raven.models.MissionUID import MissionUID  # noqa: E402
from raven.models.OfflineChatMessage import OfflineChatMessage  # noqa: E402,F401
from raven.models.Point import Point  # noqa: E402
from raven.models.RBLine import RBLine  # noqa: E402
from raven.models.RetentionSetting import RetentionSetting  # noqa: E402,F401
from raven.models.SupportingDocument import SupportingDocument  # noqa: E402,F401
from raven.models.Team import Team  # noqa: E402
from raven.models.ZMIST import ZMIST  # noqa: E402


def _upsert_mission_uid_and_change(*args, **kwargs):
    function = getattr(mission_change_module, "upsert_mission_uid_and_change", None)
    if function is None:
        function = mission_change_module.MissionChange.upsert_mission_uid_and_change
    return function(*args, **kwargs)


class CoTController:
    def __init__(self, context, log, database, socket_io):
        self.context = context
        self.logger = log
        self.db = database
        self.socketio = socket_io
        self.rabbit_connection = None
        self.rabbit_channel = None
        self.exchanges = ["cot_parser", "groups", "dms", "missions", "amq.topic"]
        # Detail parsers that each store one kind of map item against the event's point, in order
        self._point_item_parsers = (self.parse_alert, self.parse_casevac, self.parse_marker, self.parse_rbline)

    # --- helpers ------------------------------------------------------------------------------

    @property
    def config(self):
        return self.context.app.config

    def _properties(self):
        return pika.BasicProperties(expiration=str(self.config.get("RAVEN_RABBITMQ_TTL", "86400000")))

    def _publish(self, exchange, routing_key, message):
        body = message if isinstance(message, bytes) else json.dumps(message)
        self.rabbit_channel.basic_publish(
            exchange=exchange, routing_key=routing_key, body=body, properties=self._properties()
        )

    def _emit(self, event_name, data):
        socket_io = getattr(self, "socketio", None)
        if socket_io is None:
            return
        try:
            socket_io.emit(event_name, data, namespace=SOCKETIO_NAMESPACE)
        except Exception as e:
            self.logger.error(f"Failed to emit {event_name}: {e}")

    def _node_id(self):
        return self.config.get("RAVEN_NODE_ID")

    # --- RabbitMQ -----------------------------------------------------------------------------

    def run(self):
        config = self.config
        credentials = pika.PlainCredentials(
            config.get("RAVEN_RABBITMQ_USERNAME", "guest"), config.get("RAVEN_RABBITMQ_PASSWORD", "guest")
        )
        self.rabbit_connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=config.get("RAVEN_RABBITMQ_SERVER_ADDRESS", "127.0.0.1"), credentials=credentials
            )
        )
        self.rabbit_channel = self.rabbit_connection.channel()
        self.rabbit_channel.exchange_declare(exchange="cot_parser", exchange_type="direct", durable=True)
        self.rabbit_channel.queue_declare(queue="cot_parser")
        self.rabbit_channel.queue_bind(queue="cot_parser", exchange="cot_parser", routing_key="cot_parser")
        self.rabbit_channel.basic_qos(prefetch_count=int(config.get("RAVEN_RABBITMQ_PREFETCH", 2)))
        self.rabbit_channel.basic_consume(queue="cot_parser", on_message_callback=self.on_message, auto_ack=False)
        self.logger.info("cot_parser consuming")
        self.rabbit_channel.start_consuming()

    def on_message(self, channel, basic_deliver, properties, body):
        try:
            body = json.loads(body)
            if body.get("disconnected"):
                self.send_disconnect_cot(
                    body.get("uid"), body.get("user_id"), notice_time=_notice_time(body, properties)
                )
                channel.basic_ack(delivery_tag=basic_deliver.delivery_tag)
                return

            soup = BeautifulSoup(body["cot"], "xml")
            event = soup.find("event")
            if not event:
                channel.basic_ack(delivery_tag=basic_deliver.delivery_tag)
                return

            uid = body["uid"] or event.attrs.get("uid")
            if uid == self._node_id():
                uid = None
            user_id = body.get("user_id")
            target_groups_rejected = bool(body.get("target_groups_rejected"))

            with self.context:
                cot_pk = self.insert_cot(soup, event, uid)
                point_pk = self.parse_point(event, uid, cot_pk)
                self.parse_geochat(event, cot_pk, point_pk)
                self.queue_offline_chat(
                    event, uid, body.get("user_id"), body.get("target_groups"), target_groups_rejected
                )
                self.parse_video(event, cot_pk, user_id)
                for item_parser in self._point_item_parsers:
                    item_parser(event, uid, point_pk, cot_pk)
                self.parse_stats(event, uid)
                self.generate_mission_change(uid, event)
                self.record_fileshare_recipients(event, uid, user_id)
                self.route_cot(event, uid, user_id, body.get("target_groups"), target_groups_rejected)
                if "federated_from" in body:
                    self.update_federated_eud(event, uid, body["federated_from"])
        except Exception as e:
            self.logger.error(f"Failed to parse CoT: {e}")
            self.logger.debug(traceback.format_exc())
            try:
                with self.context:
                    self.db.session.rollback()
            except Exception:
                pass
            metrics.inc("raven_cot_parse_errors_total")
            channel.basic_nack(delivery_tag=basic_deliver.delivery_tag, requeue=False)
            return

        channel.basic_ack(delivery_tag=basic_deliver.delivery_tag)
        metrics.inc("raven_cot_parsed_total", metrics.CATEGORY_LABELS[metrics.cot_category(event.attrs.get("type"))])

        if event.attrs.get("type") == "t-x-d-d":
            self.mark_eud_offline(event, uid, body.get("federated_from"))

    # --- storage steps --------------------------------------------------------------------------

    def insert_cot(self, soup, event, uid):
        with self.context:
            dest = event.find("dest")
            mission_name = dest.attrs.get("mission") if dest else None
            cot = CoT(
                how=event["how"],
                type=event["type"],
                uid=event["uid"],
                sender_uid=uid,
                timestamp=_event_time(event["time"]),
                start=_event_time(event["start"]),
                stale=_event_time(event["stale"]),
                xml=str(soup),
                mission_name=mission_name or None,
            )
            try:
                self.db.session.add(cot)
                self.db.session.commit()
                return cot.id
            except sqlalchemy.exc.IntegrityError as e:
                self.db.session.rollback()
                self.logger.debug(f"CoT {event['uid']} not stored: {e}")
                return None

    def parse_point(self, event, uid, cot_id):
        point_tag = event.find("point")
        if not point_tag or str(point_tag.attrs.get("lat", "")).startswith("999"):
            return None

        latitude = float(point_tag["lat"])
        longitude = float(point_tag["lon"])
        ce, hae, le = float(point_tag["ce"]), float(point_tag["hae"]), float(point_tag["le"])
        if latitude == 0 and longitude == 0:
            return None

        with self.context:
            point = Point(
                uid=event["uid"],
                device_uid=uid,
                cot_id=cot_id,
                latitude=latitude,
                longitude=longitude,
                ce=ce,
                hae=hae,
                le=le,
                timestamp=_event_time(event["time"]),
            )

            track = event.find("track")
            if track:
                point.course = self._track_value(track, "course")
                point.speed = self._track_value(track, "speed")

            sensor = event.find("sensor")
            if sensor:
                if "azimuth" in sensor.attrs:
                    point.azimuth = _float_or_none(sensor["azimuth"])
                if "fov" in sensor.attrs:
                    point.fov = _float_or_none(sensor["fov"])

            precision = event.find("precisionlocation")
            if precision and precision.attrs.get("geolocationsrc"):
                point.location_source = precision["geolocationsrc"]
            elif precision and precision.attrs.get("altsrc"):
                point.location_source = precision["altsrc"]
            elif event.attrs.get("how") == "m-g":
                point.location_source = "GPS"

            status_tag = event.find("status")
            if status_tag and "battery" in status_tag.attrs:
                point.battery = status_tag["battery"]

            self.db.session.add(point)

            # iTAK adds content to a mission over REST before the item's CoT arrives
            usericon = event.find("usericon")
            color = event.find("color")
            contact = event.find("contact")
            iconset_path = None
            if usericon:
                iconset_path = usericon.attrs.get("iconsetpath") or usericon.attrs.get("iconsetPath")
            color_value = None
            if color:
                color_value = color.attrs.get("value") or color.attrs.get("argb")
            self.db.session.execute(
                update(MissionUID)
                .where(MissionUID.uid == event["uid"])
                .values(
                    cot_type=event["type"],
                    latitude=latitude,
                    longitude=longitude,
                    iconset_path=iconset_path,
                    color=color_value,
                    callsign=contact.attrs.get("callsign") if contact else None,
                )
            )
            self.db.session.commit()
            point_pk = point.id

            if event.find("takv") or event.find("__video"):
                stored = self.db.session.get(Point, point_pk)
                self._emit("point", stored.to_json())

            if self.config.get("RAVEN_ENABLE_MESHTASTIC"):
                try:
                    self._publish_position(event, uid, point_pk)
                except Exception as e:
                    self.db.session.rollback()
                    self.logger.error(f"Failed to publish position to Meshtastic: {e}")
                    self.logger.debug(traceback.format_exc())

            return point_pk

    @staticmethod
    def _track_value(track, name):
        value = track.attrs.get(name)
        if value is None or value == "9999999.0":
            return 0
        return float(value)

    def parse_geochat(self, event, cot_id, point_pk):
        # Must only use self.context, self.logger and self.db (a test calls it with a stand-in self)
        chat = event.find("__chat")
        remarks = event.find("remarks")
        if not chat or not remarks:
            return

        chatgrp = event.find("chatgrp")
        sender_uid = chatgrp["uid0"]
        chatroom_id = chat["id"]
        chatroom_name = chat["chatroom"]
        remarks_time = _event_time(remarks["time"])
        text = remarks.text or ""

        with self.context:
            session = self.db.session
            insert_ignore(
                session,
                Chatroom,
                {"id": chatroom_id, "name": chatroom_name, "parent": chat.attrs.get("parent") or None},
                ["id"],
            )
            session.commit()

            stored_text = text
            if len(stored_text) > GEOCHAT_REMARKS_LENGTH:
                # Stored shortened; the event itself is still routed in full
                self.logger.warning(
                    f"GeoChat {event['uid']} is {len(text)} characters, storing the first {GEOCHAT_REMARKS_LENGTH}"
                )
                stored_text = stored_text[:GEOCHAT_REMARKS_LENGTH]
            try:
                session.add(
                    GeoChat(
                        uid=event["uid"],
                        chatroom_id=chatroom_id,
                        sender_uid=sender_uid,
                        remarks=stored_text,
                        timestamp=remarks_time,
                        point_id=point_pk,
                        cot_id=cot_id,
                    )
                )
                session.commit()
            except (sqlalchemy.exc.IntegrityError, sqlalchemy.exc.DataError) as e:
                session.rollback()
                self.logger.debug(f"GeoChat {event['uid']} not stored: {e}")

            if self.context.app.config.get("RAVEN_ENABLE_MESHTASTIC"):
                try:
                    self._publish_geochat(event, chat, chatgrp, text)
                except Exception as e:
                    self.logger.error(f"Failed to publish MQTT message: {e}")

            group_owner = str(chat.attrs.get("groupOwner", "")).lower() == "true"
            for name, value in chatgrp.attrs.items():
                if not name.startswith("uid") or name == "uid":
                    continue
                if group_owner and name == "uid0":
                    session.execute(update(Chatroom).where(Chatroom.id == chatroom_id).values(group_owner=value))
                    session.commit()
                try:
                    insert_ignore(
                        session, ChatroomsUids, {"chatroom_id": chatroom_id, "uid": value}, ["chatroom_id", "uid"]
                    )
                    session.commit()
                except sqlalchemy.exc.IntegrityError as e:
                    session.rollback()
                    self.logger.debug(f"Chatroom member {value} not stored: {e}")

    def queue_offline_chat(self, event, uid, user_id, target_groups=None, target_groups_rejected=False):
        if not uid or not event.find("__chat"):
            return
        groups, rejected = _filter_target_groups(target_groups, target_groups_rejected)
        if rejected and not groups and not event.find("dest"):
            # Don't fall back to the federation "Server" account's own groups
            return
        with self.context:
            offline_chat.queue_offline_chat(
                self.db.session, event, uid, user_id, target_groups=groups if target_groups is not None else None
            )

    def parse_video(self, event, cot_pk, user_id=None):
        if not event.find("__video"):
            return
        with self.context:
            cot_sharing.upsert_cot_video(self.db.session, event, cot_pk, user_id, self.logger)

    def parse_alert(self, event, uid, point_pk, cot_pk):
        emergency = event.find("emergency")
        if not emergency:
            return

        with self.context:
            if "type" in emergency.attrs:
                alert = Alert(
                    sender_uid=uid,
                    uid=event["uid"],
                    start_time=_event_time(event["start"]),
                    point_id=point_pk,
                    alert_type=emergency["type"],
                    cot_id=cot_pk,
                    cancel_time=None,
                )
                self.db.session.add(alert)
                self.db.session.commit()
                self._emit("alert", alert.to_json())
            elif "cancel" in emergency.attrs:
                try:
                    alert = self.db.session.execute(
                        select(Alert)
                        .where(Alert.cancel_time.is_(None), Alert.sender_uid == uid)
                        .order_by(Alert.start_time.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                    if alert is None:
                        self.logger.error(f"No open alert from {uid} to cancel")
                        return
                    alert.cancel_time = _event_time(event["start"])
                    self.db.session.commit()
                    self._emit("alert", alert.to_json())
                except Exception as e:
                    self.db.session.rollback()
                    self.logger.error(f"Failed to cancel alert: {e}")

    def parse_casevac(self, event, uid, point_pk, cot_pk):
        medevac = event.find("_medevac_")
        if not medevac:
            return

        attributes = {}
        for name, value in medevac.attrs.items():
            if isinstance(value, str) and value.lower() in ("true", "false"):
                value = value.lower() == "true"
            attributes[name] = value

        zmist_tag = medevac.find("zMist")
        zmist_values = dict(zmist_tag.attrs) if zmist_tag else None

        with self.context:
            session = self.db.session
            try:
                casevac = CasEvac(
                    timestamp=_event_time(event["start"]),
                    sender_uid=uid,
                    uid=event["uid"],
                    point_id=point_pk,
                    cot_id=cot_pk,
                    **attributes,
                )
                session.add(casevac)
                if zmist_values is not None:
                    session.add(ZMIST(casevac_uid=event["uid"], **zmist_values))
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                session.rollback()
                if attributes:
                    session.execute(update(CasEvac).where(CasEvac.uid == event["uid"]).values(**attributes))
                if zmist_values is not None:
                    # Only this CASEVAC's ZMIST row
                    result = session.execute(
                        update(ZMIST).where(ZMIST.casevac_uid == event["uid"]).values(**zmist_values)
                    )
                    if not result.rowcount:
                        session.add(ZMIST(casevac_uid=event["uid"], **zmist_values))
                session.commit()

            try:
                casevac = session.execute(select(CasEvac).where(CasEvac.uid == event["uid"])).scalar_one()
                self._emit("casevac", casevac.to_json())
            except Exception as e:
                self.logger.error(f"Failed to announce CASEVAC: {e}")

    def parse_marker(self, event, uid, point_pk, cot_pk):
        try:
            cot_type = event.attrs.get("type", "")
            if not MARKER_TYPE.match(cot_type) or cot_type == "b-m-p-s-p-loc" or event.find("takv"):
                return
            if any("endpoint" in contact.attrs for contact in event.find_all("contact")):
                return

            with self.context:
                self._store_marker(event, cot_type, point_pk, cot_pk)
        except Exception as e:
            try:
                with self.context:
                    self.db.session.rollback()
            except Exception:
                pass
            self.logger.error(f"Failed to parse marker: {e}")
            self.logger.debug(traceback.format_exc())

    def _default_icon_id(self):
        icon = self.db.session.execute(
            select(Icon).where(Icon.filename == "marker-icon.png").limit(1)
        ).scalar_one_or_none()
        return icon.id if icon else None

    def _store_marker(self, event, cot_type, point_pk, cot_pk):
        session = self.db.session
        marker = Marker(
            uid=event["uid"],
            affiliation=get_affiliation(cot_type),
            battle_dimension=get_battle_dimension(cot_type),
            mil_std_2525c=cot_type_to_2525c(cot_type),
        )
        icon_id = None

        detail = event.find("detail")
        if detail:
            for tag in detail.find_all(True):
                attrs = tag.attrs
                if "readiness" in attrs:
                    marker.readiness = attrs["readiness"] == "true"
                if "argb" in attrs:
                    marker.argb = int(attrs["argb"])
                    marker.color_hex = _hex8(attrs["argb"])
                if tag.name == "contact":
                    marker.callsign = attrs["callsign"]
                if "iconsetpath" in attrs:
                    iconset_path = attrs["iconsetpath"]
                    marker.iconset_path = iconset_path
                    if iconset_path.lower().endswith(".png"):
                        filename = iconset_path.split("/")[-1]
                        icon = session.execute(
                            select(Icon).where(Icon.filename == filename).limit(1)
                        ).scalar_one_or_none()
                        icon_id = icon.id if icon else self._default_icon_id()
                    elif not marker.mil_std_2525c:
                        icon_id = self._default_icon_id()
                if "altsrc" in attrs:
                    marker.location_source = attrs["altsrc"]

        link = event.find("link")
        if link:
            marker.parent_callsign = link.attrs.get("parent_callsign")
            marker.production_time = link.attrs.get("production_time") or iso8601_string_from_datetime(
                datetime.now(timezone.utc)
            )
            marker.relation = link.attrs.get("relation")
            marker.relation_type = link.attrs.get("relation_type")
            marker.parent_uid = link.attrs.get("uid")
        else:
            marker.production_time = iso8601_string_from_datetime(datetime.now(timezone.utc))

        marker.icon_id = icon_id
        marker.point_id = point_pk
        marker.cot_id = cot_pk

        values = {field: getattr(marker, field) for field in MARKER_UPDATE_FIELDS}
        try:
            session.add(marker)
            session.commit()
        except sqlalchemy.exc.IntegrityError:
            session.rollback()
            session.execute(
                update(Marker).where(Marker.uid == event["uid"]).values(point_id=point_pk, icon_id=icon_id, **values)
            )
            session.commit()
            marker = session.execute(select(Marker).where(Marker.uid == event["uid"])).scalar_one()

        self._emit("marker", marker.to_json())

    def parse_rbline(self, event, uid, point_pk, cot_pk):
        if not RB_LINE_TYPE.match(event.attrs.get("type", "")):
            return
        detail = event.find("detail")
        if not detail:
            return

        values = {}
        for child in detail.find_all(True, recursive=False):
            name = child.name
            value = child.attrs.get("value")
            if name in RB_LINE_VALUES:
                column, convert = RB_LINE_VALUES[name]
                values[column] = convert(value) if value is not None else None
            elif name == "inclination":
                # Digits only: ATAK may send NaN
                if value is not None and value.isdigit():
                    values["inclination"] = float(value)
            elif name == "color":
                values["color"] = int(float(value)) if value is not None else None
                if value is not None:
                    values["color_hex"] = _hex8(int(float(value)))
            elif name == "contact":
                values["callsign"] = child.attrs.get("callsign")
            elif name == "labels_on":
                values["labels_on"] = value == "true"

        with self.context:
            session = self.db.session
            start_point = session.get(Point, point_pk) if point_pk is not None else None
            if start_point is None:
                raise ValueError(f"R&B line {event['uid']} has no start point")

            if values.get("bearing_units") is None:
                raise ValueError(f"R&B line {event['uid']} has no bearingUnits")

            rb_line = RBLine(
                uid=event["uid"],
                sender_uid=uid,
                timestamp=_event_time(event["start"]),
                point_id=point_pk,
                cot_id=cot_pk,
                **values,
            )
            end_point = rb_line.calc_end_point(start_point)
            rb_line.end_latitude = _scalar(end_point["latitude"])
            rb_line.end_longitude = _scalar(end_point["longitude"])
            update_values = {field: getattr(rb_line, field, None) for field in RB_LINE_UPDATE_FIELDS}
            try:
                session.add(rb_line)
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                session.rollback()
                session.execute(update(RBLine).where(RBLine.uid == event["uid"]).values(**update_values))
                session.commit()
                rb_line = session.execute(select(RBLine).where(RBLine.uid == event["uid"])).scalar_one()

            rb_line.point = start_point
            self._emit("rb_line", rb_line.to_json())

    def parse_stats(self, event, uid):
        stats = event.find("stats")
        if not stats:
            return

        with self.context:
            row = EUDStats(
                timestamp=_event_time(event["time"]),
                eud_uid=uid,
                battery_status=stats.attrs.get("battery_status"),
                ip_address=stats.attrs.get("ip_address"),
            )
            for name in STATS_INT_ATTRS:
                value = stats.attrs.get(name)
                if name == "battery" and value is not None:
                    value = value.replace("%", "")
                if value:
                    setattr(row, name, _int_attr(value))
            self.db.session.add(row)
            self.db.session.commit()

    def generate_mission_change(self, uid, event):
        for dest in event.find_all("dest"):
            mission_name = dest.attrs.get("mission")
            if not mission_name:
                continue

            with self.context:
                mission = self.db.session.execute(
                    select(Mission).where(Mission.name == mission_name)
                ).scalar_one_or_none()
                if mission is None:
                    self.logger.error(f"No such mission found: {mission_name}")
                    continue

                routing_key = f"missions.{mission_name}"
                self._publish("missions", routing_key, {"uid": uid, "cot": str(event)})

                contact = event.find("contact")
                usericon = event.find("usericon")
                color = event.find("color")
                iconset_path = None
                if usericon:
                    iconset_path = usericon.attrs.get("iconsetpath") or usericon.attrs.get("iconsetPath")
                color_value = None
                if color:
                    color_value = color.attrs.get("argb") or color.attrs.get("value")

                latitude = longitude = None
                point = event.find("point")
                if point:
                    lat, lon = point.attrs.get("lat"), point.attrs.get("lon")
                    if lat and lon and not lat.startswith("999") and not lon.startswith("999"):
                        latitude, longitude = _float_or_none(lat), _float_or_none(lon)
                        if latitude is None or longitude is None:
                            latitude = longitude = None

                _, _, change_cot = _upsert_mission_uid_and_change(
                    mission_name,
                    mission,
                    event["uid"],
                    uid,
                    _event_time(event["start"]),
                    cot_type=event["type"],
                    callsign=contact.attrs.get("callsign") if contact else None,
                    iconset_path=iconset_path,
                    color=color_value,
                    latitude=latitude,
                    longitude=longitude,
                )
                if not isinstance(change_cot, str):
                    change_cot = tostring(change_cot).decode() if isinstance(change_cot, Element) else str(change_cot)
                self._publish("missions", routing_key, {"uid": self._node_id(), "cot": change_cot})

    def record_fileshare_recipients(self, event, uid, user_id=None):
        if not parse_fileshare(event, uid):
            return
        with self.context:
            try:
                cot_sharing.record_fileshare_recipients(self.db.session, event, uid, user_id, self.logger)
            except Exception as e:
                self.logger.error(f"Failed to record fileshare recipients: {e}")
                self.db.session.rollback()

    # --- routing ------------------------------------------------------------------------------

    def route_cot(self, event, uid, user_id, target_groups=None, target_groups_rejected=False):
        if not uid or uid == self._node_id():
            return

        message = {"uid": uid, "cot": str(event)}
        groups, rejected = _filter_target_groups(target_groups, target_groups_rejected)
        dests = event.find_all("dest")

        if not dests and rejected and not groups:
            return

        if not dests and groups:
            for group in groups:
                self._publish("groups", f"{group}.OUT", message)
            return

        if dests:
            for dest in dests:
                callsign = dest.attrs.get("callsign")
                dest_uid = dest.attrs.get("uid")
                if callsign:
                    self._publish("dms", callsign, message)
                elif dest_uid and dest_uid != uid:
                    self._publish("dms", dest_uid, message)
            return

        if not user_id:
            self._publish("groups", "__ANON__.OUT", message)
            return

        with self.context:
            group_names = self._group_names(user_id, "IN")
        if not group_names:
            self._publish("groups", "__ANON__.OUT", message)
            return
        for group in group_names:
            self._publish("groups", f"{group}.OUT", message)

    def _group_names(self, user_id, direction):
        return list(
            self.db.session.execute(
                select(Group.name)
                .join(GroupUser, GroupUser.group_id == Group.id)
                .where(
                    GroupUser.user_id == user_id,
                    GroupUser.direction == direction,
                    GroupUser.enabled.is_(True),
                )
            ).scalars()
        )

    # --- EUD status ---------------------------------------------------------------------------

    def update_federated_eud(self, event, uid, federated_from):
        try:
            if not uid or event.attrs.get("type") == "t-x-d-d" or event.attrs.get("uid") != uid:
                return
            takv = event.find("takv")
            if not takv:
                return

            with self.context:
                session = self.db.session
                eud = session.execute(select(EUD).where(EUD.uid == uid)).scalar_one_or_none()
                if eud is None:
                    return
                if eud.federated_from is None and eud.last_status == "Connected":
                    # A live local connection owns this row
                    return

                announce = eud.last_status != "Connected" or eud.federated_from != federated_from
                eud.last_status = "Connected"
                eud.last_event_time = _event_time(event["start"])
                eud.federated_from = federated_from
                for field in ("device", "os", "platform", "version"):
                    value = takv.attrs.get(field)
                    if value:
                        setattr(eud, field, _strip_tags(value))

                group = event.find("__group")
                if group and group.attrs.get("name"):
                    team = session.execute(select(Team).where(Team.name == group["name"])).scalar_one_or_none()
                    if team is not None:
                        eud.team_id = team.id
                        if group.attrs.get("role"):
                            eud.team_role = _strip_tags(group["role"])

                session.commit()
                if announce:
                    self._emit("eud", eud.to_json())
        except Exception as e:
            self._safe_rollback()
            self.logger.error(f"Failed to update federated EUD {uid}: {e}")
            self.logger.debug(traceback.format_exc())

    @staticmethod
    def _is_own_disconnect(event, uid) -> bool:
        """A t-x-d-d that says the sending device itself went away, as opposed to ATAK deleting
        a map item (which also uses t-x-d-d, with the item's uid in <link>)."""
        links = event.find_all("link")
        if any(link.attrs.get("uid") == uid for link in links):
            return True
        return not links and event.attrs.get("uid") == uid

    def mark_eud_offline(self, event, uid, federated_from=None):
        try:
            if not uid or not self._is_own_disconnect(event, uid):
                return
            with self.context:
                session = self.db.session
                eud = session.execute(select(EUD).where(EUD.uid == uid)).scalar_one_or_none()
                if eud is None:
                    return
                if federated_from and eud.federated_from is None and eud.last_status == "Connected":
                    return
                eud.last_event_time = _event_time(event["start"])
                eud.last_status = "Disconnected"
                session.commit()
                self._emit("eud", eud.to_json())
        except Exception as e:
            self._safe_rollback()
            self.logger.error(f"Failed to mark {uid} offline: {e}")
            self.logger.debug(traceback.format_exc())

    def _safe_rollback(self):
        try:
            with self.context:
                self.db.session.rollback()
        except Exception:
            pass

    def send_disconnect_cot(self, uid, user_id, notice_time=None):
        """Announces that a device's connection closed and drops its queue's bindings.

        notice_time is when the connection closed, if the notice says so. The notice may be
        processed late (a backlog): if the device has reconnected meanwhile, its new session
        owns the queue's bindings and its status, so neither is touched."""
        if not uid:
            return

        now = datetime.now(timezone.utc)
        now_string = iso8601_string_from_datetime(now)
        event = Element(
            "event",
            {
                "how": "h-g-i-g-o",
                "type": "t-x-d-d",
                "version": "2.0",
                "uid": str(uuid.uuid4()),
                "start": now_string,
                "time": now_string,
                "stale": iso8601_string_from_datetime(now + timedelta(seconds=10)),
            },
        )
        add_unknown_point(event)
        detail = SubElement(event, "detail")
        SubElement(detail, "link", {"relation": "p-p", "uid": uid, "type": "a-f-G-U-C"})
        SubElement(detail, "_flow-tags_", {FLOW_TAG: now_string})
        message = {"uid": uid, "cot": tostring(event).decode()}

        channel = self.rabbit_channel
        with self.context:
            bindings = []
            if channel is not None and channel.is_open and user_id:
                for group in self._group_names(user_id, "OUT"):
                    self._publish("groups", f"{group}.OUT", message)
                    bindings.append(("groups", f"{group}.OUT"))
            elif channel is not None and channel.is_open and not getattr(channel, "is_closing", False):
                self._publish("groups", "__ANON__.OUT", message)
                bindings.append(("groups", "__ANON__.OUT"))

            if self._has_reconnected(uid, notice_time):
                self.logger.info(f"{uid} is connected again: leaving its queue bindings and status alone")
                return

            for mission_name in self.db.session.execute(select(Mission.name)).scalars():
                bindings.append(("missions", f"missions.{mission_name}"))

            self._unbind_device_queue(uid, bindings)

            self.db.session.execute(
                update(EUD).where(EUD.uid == uid).values(last_status="Disconnected", last_event_time=_now())
            )
            self.db.session.commit()

    def _has_reconnected(self, uid, notice_time) -> bool:
        """True when the device's row shows a live session again: Connected, or heard from
        (last_rx_at / last_event_time) after the connection the notice is about closed.

        The closing EudHandler marks its row Disconnected (and unbinds its own queue) right after
        publishing the notice, so a Connected row normally means a new session; in the rare case
        that this runs before that commit, skipping is harmless for the same reason."""
        eud = self.db.session.execute(select(EUD).where(EUD.uid == uid)).scalar_one_or_none()
        if eud is None:
            return False
        if eud.last_status == "Connected":
            return True
        if notice_time is None:
            return False
        heard = [t for t in (eud.last_rx_at, eud.last_event_time) if t is not None]
        return any(_utc_naive(t) > notice_time for t in heard)

    def _unbind_device_queue(self, uid, bindings):
        """Unbinds the device's queue on a channel of its own: if the queue is gone already the
        broker closes that channel, which must not take the consuming channel down with it."""
        if not bindings:
            return
        connection = self.rabbit_connection
        if connection is None or not connection.is_open:
            self.logger.debug(f"No connection to unbind {uid}'s queue on")
            return

        channel = None
        try:
            channel = connection.channel()
            try:
                channel.queue_declare(queue=uid, passive=True)
            except pika.exceptions.ChannelClosedByBroker as e:
                self.logger.debug(f"{uid} has no queue to unbind ({e.reply_code})")
                return
            for exchange, routing_key in bindings:
                channel.queue_unbind(queue=uid, exchange=exchange, routing_key=routing_key)
                self.logger.debug(f"Unbound {uid} from {exchange} {routing_key}")
        except pika.exceptions.AMQPError as e:
            self.logger.warning(f"Failed to unbind {uid}'s queue: {e!r}")
        finally:
            if channel is not None and channel.is_open:
                try:
                    channel.close()
                except pika.exceptions.AMQPError:
                    pass

    # --- Meshtastic ---------------------------------------------------------------------------

    def _meshtastic_id_of(self, uid):
        eud = self.db.session.execute(select(EUD).where(EUD.uid == uid)).scalar_one_or_none()
        if eud is not None and eud.meshtastic_id:
            return int(eud.meshtastic_id)
        return random.getrandbits(32)

    def get_protobuf(self, payload, uid=None, from_id=None, to_id=BROADCAST_NUM, channel_id="LongFast"):
        if isinstance(from_id, str):
            sender = int(from_id.lstrip("!"), 16)
        elif isinstance(from_id, int):
            sender = from_id
        elif uid:
            with self.context:
                sender = self._meshtastic_id_of(uid)
        else:
            sender = random.getrandbits(32)

        packet = mesh_pb2.MeshPacket()
        packet.id = random.getrandbits(32)
        setattr(packet, "from", sender & 0xFFFFFFFF)
        packet.to = to_id
        packet.want_ack = False
        packet.hop_limit = 3
        packet.decoded.CopyFrom(payload)

        envelope = mqtt_pb2.ServiceEnvelope()
        envelope.packet.CopyFrom(packet)
        envelope.channel_id = channel_id
        envelope.gateway_id = "Raven"
        return envelope

    def get_meshtastic_channels(self):
        with self.context:
            return list(
                self.db.session.execute(
                    select(MeshtasticChannel.name).where(MeshtasticChannel.downlink_enabled.is_(True))
                ).scalars()
            )

    def publish_to_meshtastic(self, body):
        """Publishes a ServiceEnvelope once per downlink-enabled channel."""
        topic = self.config.get("RAVEN_MESHTASTIC_TOPIC", "raven")
        for channel_name in self.get_meshtastic_channels():
            envelope = mqtt_pb2.ServiceEnvelope()
            envelope.CopyFrom(body)
            envelope.channel_id = channel_name
            self._publish("amq.topic", f"{topic}.2.e.{channel_name}.outgoing", envelope.SerializeToString())

    def _publish_position(self, event, uid, point_pk):
        if not uid:
            return
        session = self.db.session
        eud = session.execute(select(EUD).where(EUD.uid == uid)).scalar_one_or_none()
        if eud is None or eud.platform == "Meshtastic":
            return

        now = _now()
        interval = int(self.config.get("RAVEN_MESHTASTIC_PUBLISH_INTERVAL", 30))
        if eud.last_meshtastic_publish and (now - eud.last_meshtastic_publish).total_seconds() < interval:
            return
        eud.last_meshtastic_publish = now
        session.commit()

        point = session.get(Point, point_pk)
        timestamp = point.timestamp.replace(tzinfo=timezone.utc) if point.timestamp.tzinfo is None else point.timestamp
        course = int(float(point.course)) if point.course else 0
        speed = int(float(point.speed)) if point.speed else 0
        speed = speed if speed >= 0 else 0
        altitude = int(float(point.hae)) if point.hae is not None else 0

        position = mesh_pb2.Position(
            latitude_i=int(point.latitude / 1e-7),
            longitude_i=int(point.longitude / 1e-7),
            altitude=altitude,
            timestamp=int(timestamp.timestamp()),
            ground_track=course,
            ground_speed=speed,
            seq_number=1,
            precision_bits=32,
        )
        data = mesh_pb2.Data(portnum=portnums_pb2.POSITION_APP, payload=position.SerializeToString())
        self.publish_to_meshtastic(self.get_protobuf(data, uid=uid))

        tak_packet = atak_pb2.TAKPacket()
        tak_packet.is_compressed = True
        tak_packet.contact.device_callsign = _compress(eud.uid)
        tak_packet.contact.callsign = _compress(eud.callsign)
        tak_packet.group.CopyFrom(_tak_group(eud.team.name if eud.team else None, eud.team_role))
        if point.battery is not None:
            tak_packet.status.battery = int(float(point.battery))
        tak_packet.pli.latitude_i = int(point.latitude * 1e7)
        tak_packet.pli.longitude_i = int(point.longitude * 1e7)
        tak_packet.pli.altitude = altitude
        tak_packet.pli.speed = speed
        tak_packet.pli.course = course
        data = mesh_pb2.Data(portnum=portnums_pb2.ATAK_PLUGIN, payload=tak_packet.SerializeToString())
        from_id = int(eud.meshtastic_id) if eud.meshtastic_id else None
        self.publish_to_meshtastic(self.get_protobuf(data, uid=uid, from_id=from_id))

    def _publish_geochat(self, event, chat, chatgrp, text):
        sender_uid = chatgrp["uid0"]
        eud = self.db.session.execute(select(EUD).where(EUD.uid == sender_uid)).scalar_one_or_none()

        tak_packet = atak_pb2.TAKPacket()
        tak_packet.is_compressed = True
        tak_packet.contact.device_callsign = _compress(sender_uid)
        tak_packet.contact.callsign = _compress(eud.callsign if eud else "")
        tak_packet.group.CopyFrom(
            _tak_group(eud.team.name if eud is not None and eud.team else None, eud.team_role if eud else None)
        )
        tak_packet.chat.message = _compress(text)

        plain_text = False
        dest = event.find("dest")
        if dest and dest.attrs.get("callsign") and dest.attrs.get("callsign") == chat.attrs.get("chatroom"):
            to = chat["id"]
            try:
                to_id = int(to.lstrip("!"), 16)
                plain_text = True
            except ValueError:
                to_id = BROADCAST_NUM
        else:
            to = chat["chatroom"]
            to_id = BROADCAST_NUM
            plain_text = to == "All Chat Rooms"
        tak_packet.chat.to = to

        if plain_text:
            data = mesh_pb2.Data(portnum=portnums_pb2.TEXT_MESSAGE_APP, payload=text.encode("utf-8"))
        else:
            data = mesh_pb2.Data(portnum=portnums_pb2.ATAK_PLUGIN, payload=tak_packet.SerializeToString())

        from_id = int(eud.meshtastic_id) if eud is not None and eud.meshtastic_id else None
        self.publish_to_meshtastic(self.get_protobuf(data, uid=sender_uid, from_id=from_id, to_id=to_id))


def main():
    from flask_socketio import SocketIO

    config = app.config
    user = quote(str(config.get("RAVEN_RABBITMQ_USERNAME", "guest")), safe="")
    password = quote(str(config.get("RAVEN_RABBITMQ_PASSWORD", "guest")), safe="")
    host = config.get("RAVEN_RABBITMQ_SERVER_ADDRESS", "127.0.0.1")
    sio = SocketIO(message_queue=f"amqp://{user}:{password}@{host}")

    # Before forking: every child counts into the same shared memory
    counters = metrics.install(metrics.SharedCounters(metrics.COT_PARSER_SERIES))

    try:
        for _ in range(int(config.get("RAVEN_COT_PARSER_PROCESSES", 1))):
            try:
                pid = os.fork()
            except OSError as e:
                logger.error(f"Failed to fork a cot_parser worker: {e}")
                continue

            if pid == 0:
                try:
                    controller = CoTController(app.app_context(), logger, db, sio)
                    controller.run()
                except BaseException as e:
                    logger.error(f"cot_parser error: {e}")
                    logger.debug(traceback.format_exc())
                    os._exit(1)
                os._exit(0)

            child_processes.append(pid)
    except KeyboardInterrupt:
        pass

    # Only now: the parent must not hold a DB connection its children could share
    metrics.MetricsFlusher(app, counters, "cot_parser").start()

    for pid in child_processes:
        try:
            os.waitpid(pid, 0)
        except BaseException:
            logger.info("Exiting...")
            sys.exit(0)


if __name__ == "__main__":
    main()

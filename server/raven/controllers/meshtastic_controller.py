"""Bridge between Meshtastic MQTT traffic and TAK.

RabbitMQ's MQTT plugin maps Meshtastic topics ``<root>/2/e/<channel>/<!nodeid>`` onto the ``amq.topic``
exchange as ``<root>.2.e.<channel>.<!nodeid>``. This consumer decrypts each envelope with its channel PSK,
turns positions, node info, text, telemetry and ATAK-plugin packets into CoT plus EUD/point rows, publishes
the CoT to the internal exchanges, and re-broadcasts the untouched envelope to the other downlink channels.
Messages ending in ``outgoing`` are TAK-to-mesh traffic produced by the CoT parser and are ignored.
"""

import base64
import datetime
import json
import secrets
import uuid
import xml.etree.ElementTree as ET

import pika
import unishox2
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from meshtastic import BROADCAST_NUM, protocols
from meshtastic.protobuf import mesh_pb2, mqtt_pb2, portnums_pb2
from sqlalchemy.exc import IntegrityError

from raven.controllers.rabbitmq_client import RabbitMQClient
from raven.proto import atak_pb2

NAMESPACE = "/socket.io"
DEFAULT_PSK_SHORTHAND = "AQ=="
DEFAULT_PSK = "1PG7OiApB1nwvP+rz05pAQ=="
COT_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

PortNum = portnums_pb2.PortNum


def _hex_id(number: int) -> str:
    return "{:08x}".format(number)


def _same_node(a, b) -> bool:
    """Compare two hex node ids regardless of zero padding."""
    try:
        return int(str(a), 16) == int(str(b), 16)
    except (TypeError, ValueError):
        return False


def _unishox(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", errors="surrogateescape")
    if not data:
        return ""
    return unishox2.decompress(bytes(data), max(len(data) * 8, 256))


def _cot_time(moment: datetime.datetime) -> str:
    return moment.strftime(COT_TIME_FORMAT)


# What we know about a mesh node before it has told us anything: no fix (CoT's "unknown" altitude),
# no telemetry, and the default Cyan team member presence.
_UNKNOWN_NODE = {
    "hw_model": "",
    "short_name": "",
    "macaddr": "",
    "firmware_version": "",
    "uid": None,
    "team": "Cyan",
    "role": "Team Member",
    "last_lat": "0.0",
    "last_lon": "0.0",
    "last_alt": "9999999.0",
    "course": "0.0",
    "speed": "0.0",
    "battery": 0,
    "voltage": 0,
    "uptime": 0,
}


def _node_record(node_id: str, **known) -> dict:
    """A fresh tracking record for node_id, with any known fields laid over the defaults."""
    record = dict(_UNKNOWN_NODE, meshtastic_id=node_id)
    record.update(known)
    return record


def _new_record(node_id: str) -> dict:
    tail = node_id[-4:]
    return _node_record(node_id, short_name=tail, long_name=f"Meshtastic {tail}")


class MeshtasticController(RabbitMQClient):
    def __init__(self, context):
        self.node_names = {}
        self.meshtastic_devices = {}
        # The base constructor starts the connection, so state must exist before it is called
        self.context = context
        from raven.extensions import db, logger

        self.logger = logger
        self.db = db
        self.get_euds()
        self.get_channels()
        super().__init__(context)

    # ------------------------------------------------------------------ startup

    def get_euds(self):
        """Seed the node table with every known EUD so mesh traffic can be matched to its callsign."""
        from raven.models.EUD import EUD

        with self.context:
            session = self.db.session
            for eud in session.execute(self.db.select(EUD)).scalars().all():
                if not eud.meshtastic_id:
                    # Give the device a stable random 32-bit node number the first time we see it
                    eud.meshtastic_id = secrets.randbits(32)
                    session.commit()
                self.meshtastic_devices[eud.uid] = _node_record(
                    format(eud.meshtastic_id, "x"),
                    uid=eud.uid,
                    long_name=eud.callsign,
                    hw_model=eud.device,
                    firmware_version=eud.version,
                    macaddr=eud.meshtastic_macaddr,
                )

    def get_channels(self):
        from raven.models.Meshtastic import MeshtasticChannel

        with self.context:
            channels = {}
            rows = self.db.session.execute(self.db.select(MeshtasticChannel)).scalars().all()
            for channel in rows:
                if channel.downlink_enabled:
                    channels[channel.name] = channel.psk
            self.context.app.config["RAVEN_MESHTASTIC_DOWNLINK_CHANNELS"] = channels

    # ------------------------------------------------------------------ RabbitMQ

    @property
    def _config(self):
        return self.context.app.config

    def _ttl_properties(self):
        return pika.BasicProperties(expiration=str(self._config.get("RAVEN_RABBITMQ_TTL")))

    def on_channel_open(self, channel):
        self.rabbit_channel = channel
        channel.add_on_close_callback(self.on_close)
        channel.queue_declare(queue="meshtastic")
        channel.queue_bind(exchange="amq.topic", queue="meshtastic", routing_key="#")
        channel.basic_consume(queue="meshtastic", on_message_callback=self.on_message, auto_ack=True)
        self.is_consuming = True

    def on_message(self, unused_channel, basic_deliver, properties, body):
        routing_key = basic_deliver.routing_key or ""
        if routing_key.endswith("outgoing"):
            return
        parts = routing_key.split(".")
        if len(parts) < 4:
            return
        source_channel = parts[3]

        try:
            envelope = mqtt_pb2.ServiceEnvelope()
            envelope.ParseFromString(body)
        except Exception as e:  # noqa: BLE001
            self.logger.error("Failed to parse Meshtastic envelope on {}: {}".format(routing_key, e))
            return
        packet = envelope.packet

        from_id = _hex_id(getattr(packet, "from"))
        to_id = "all" if packet.to == BROADCAST_NUM else _hex_id(packet.to)
        if from_id not in self.meshtastic_devices:
            self.meshtastic_devices[from_id] = _new_record(from_id)

        if packet.HasField("encrypted") and not packet.HasField("decoded"):
            try:
                packet = self.decrypt(packet, source_channel)
            except Exception as e:  # noqa: BLE001
                self.logger.warning("Failed to decrypt Meshtastic packet from {}: {}".format(from_id, e))
                return

        portnum = packet.decoded.portnum
        payload = packet.decoded.payload
        known = protocols.get(portnum)
        if known is None:
            if portnum == PortNum.ATAK_PLUGIN:
                try:
                    tak_packet = atak_pb2.TAKPacket()
                    tak_packet.ParseFromString(payload)
                    self._convert_and_publish(tak_packet, from_id, to_id, portnum)
                except Exception as e:  # noqa: BLE001
                    self.logger.error("Failed to handle ATAK plugin packet from {}: {}".format(from_id, e))
            return

        try:
            if known.protobufFactory is None:
                self._convert_and_publish(payload, from_id, to_id, portnum)
            else:
                pb = known.protobufFactory()
                pb.ParseFromString(payload)
                if portnum == PortNum.NODEINFO_APP:
                    self.node_names[getattr(packet, "from")] = pb.short_name
                    if self.rabbit_channel is not None:
                        self.rabbit_channel.queue_declare(queue=from_id)
                self._convert_and_publish(pb, from_id, to_id, portnum)
        except Exception as e:  # noqa: BLE001
            self.logger.error("Failed to handle Meshtastic {} packet from {}: {}".format(known.name, from_id, e))

        self._rebroadcast(routing_key, source_channel, body)

    def _rebroadcast(self, routing_key, source_channel, body):
        topic = self._config.get("RAVEN_MESHTASTIC_TOPIC")
        for channel_name in list(self._config.get("RAVEN_MESHTASTIC_DOWNLINK_CHANNELS") or {}):
            if channel_name == source_channel:
                continue
            prefix = "{}.2.e.{}.".format(topic, channel_name)
            if routing_key.startswith(prefix):
                continue
            try:
                self.rabbit_channel.basic_publish(
                    exchange="amq.topic",
                    routing_key=prefix + "outgoing",
                    body=body,
                    properties=self._ttl_properties(),
                )
            except Exception as e:  # noqa: BLE001
                self.logger.error("Failed to re-broadcast Meshtastic packet to {}: {}".format(channel_name, e))

    def decrypt(self, mesh_packet, channel_name):
        channels = self._config.get("RAVEN_MESHTASTIC_DOWNLINK_CHANNELS") or {}
        if channel_name not in channels or not mesh_packet.HasField("encrypted"):
            return mesh_packet

        psk = channels[channel_name]
        if psk == DEFAULT_PSK_SHORTHAND:
            psk = DEFAULT_PSK
        key = base64.b64decode(psk)
        nonce = mesh_packet.id.to_bytes(8, "little") + getattr(mesh_packet, "from").to_bytes(8, "little")
        decryptor = Cipher(algorithms.AES(key), modes.CTR(nonce)).decryptor()
        plaintext = decryptor.update(mesh_packet.encrypted) + decryptor.finalize()

        data = mesh_pb2.Data()
        data.ParseFromString(plaintext)

        decrypted = mesh_pb2.MeshPacket()
        decrypted.CopyFrom(mesh_packet)
        decrypted.ClearField("encrypted")
        decrypted.decoded.CopyFrom(data)
        return decrypted

    # ------------------------------------------------------------------ conversion

    def _record(self, from_id) -> dict:
        record = self.meshtastic_devices.get(from_id)
        if record is None:
            record = _new_record(from_id)
            self.meshtastic_devices[from_id] = record
        return record

    def cot(self, pb, from_id, to_id, portnum, how="m-g", cot_type="a-f-G-U-C", uid=None):
        record = self._record(from_id)
        if not uid:
            uid = record.get("uid") or from_id
        sent_at = datetime.datetime.now(datetime.timezone.utc)
        stamp = _cot_time(sent_at)
        stale_after = _cot_time(sent_at + datetime.timedelta(days=1))

        event = ET.Element("event", how=how, type=cot_type, version="2.0", uid=uid)
        event.set("start", stamp)
        event.set("time", stamp)
        event.set("stale", stale_after)
        ET.SubElement(
            event,
            "point",
            {
                "ce": "9999999.0",
                "le": "9999999.0",
                "hae": str(record.get("last_alt")),
                "lat": str(record.get("last_lat")),
                "lon": str(record.get("last_lon")),
            },
        )
        detail = ET.SubElement(event, "detail")

        is_chat = portnum == PortNum.TEXT_MESSAGE_APP or (
            portnum == PortNum.ATAK_PLUGIN and isinstance(pb, atak_pb2.TAKPacket) and pb.HasField("chat")
        )
        if is_chat:
            return event

        ET.SubElement(
            detail,
            "takv",
            {
                "device": str(record.get("hw_model") or ""),
                "version": str(record.get("firmware_version") or ""),
                "platform": "Meshtastic",
                "os": "Meshtastic",
                "macaddr": str(record.get("macaddr") or ""),
                "meshtastic_id": str(record.get("meshtastic_id") or ""),
            },
        )
        ET.SubElement(detail, "contact", {"callsign": str(record.get("long_name")), "endpoint": "MQTT"})
        ET.SubElement(detail, "uid", {"Droid": str(record.get("long_name"))})
        ET.SubElement(detail, "precisionlocation", {"altsrc": "GPS", "geopointsrc": "GPS"})
        ET.SubElement(detail, "status", {"battery": str(record.get("battery"))})
        ET.SubElement(
            detail, "track", {"course": str(record.get("course") or "0.0"), "speed": str(record.get("speed") or "0.0")}
        )
        ET.SubElement(detail, "__group", {"name": str(record.get("team")), "role": str(record.get("role"))})
        return event

    def insert_or_update_eud(self, uid, from_id, update_if_exists=True):
        from raven.models.EUD import EUD
        from raven.models.Team import Team

        record = self._record(from_id)
        with self.context:
            eud = EUD()
            eud.uid = uid
            eud.callsign = record.get("long_name")
            eud.device = str(record.get("hw_model")) if record.get("hw_model") is not None else None
            eud.os = "Meshtastic"
            eud.platform = "Meshtastic"
            eud.version = record.get("firmware_version")
            eud.team_role = record.get("role")
            eud.meshtastic_id = int(str(record.get("meshtastic_id")), 16)
            eud.meshtastic_macaddr = record.get("macaddr")

            team = self.db.session.execute(self.db.select(Team).filter_by(name=record.get("team"))).scalar_one_or_none()
            if team is not None:
                eud.team_id = team.id

            try:
                self.socketio.emit("eud", eud.to_json(), namespace=NAMESPACE)
            except Exception as e:  # noqa: BLE001
                self.logger.error("Failed to emit Meshtastic EUD {}: {}".format(uid, e))

            try:
                self.db.session.add(eud)
                self.db.session.commit()
            except IntegrityError:
                self.db.session.rollback()
                if update_if_exists:
                    self.db.session.execute(self.db.update(EUD).where(EUD.uid == uid).values(**eud.serialize()))
                    self.db.session.commit()

    def position(self, pb, from_id, to_id, portnum):
        from raven.models.EUD import EUD
        from raven.models.Point import Point

        record = self._record(from_id)
        try:
            if portnum == PortNum.MAP_REPORT_APP and pb.firmware_version != record.get("firmware_version"):
                try:
                    with self.context:
                        eud = self.db.session.execute(self.db.select(EUD).filter_by(uid=from_id)).scalar_one()
                        eud.version = pb.firmware_version
                        eud.device = str(pb.hw_model)
                        eud.callsign = pb.long_name
                        self.db.session.commit()
                except Exception as e:  # noqa: BLE001
                    self.logger.debug("Could not update Meshtastic EUD {} from map report: {}".format(from_id, e))
                try:
                    record["firmware_version"] = pb.firmware_version
                    record["hw_model"] = mesh_pb2.HardwareModel.Name(pb.hw_model)
                    record["long_name"] = pb.long_name or "Meshtastic {}".format(from_id)
                    record["short_name"] = pb.short_name or from_id[-4:]
                except Exception as e:  # noqa: BLE001
                    self.logger.debug("Could not apply map report from {}: {}".format(from_id, e))

            record["last_lat"] = pb.latitude_i * 1e-7
            record["last_lon"] = pb.longitude_i * 1e-7
            record["last_alt"] = pb.altitude
            if portnum == PortNum.POSITION_APP:
                record["course"] = pb.ground_track or "0.0"
                record["speed"] = pb.ground_speed or "0.0"

            self.insert_or_update_eud(from_id, from_id, update_if_exists=False)

            with self.context:
                point = Point(
                    uid=str(uuid.uuid4()),
                    device_uid=from_id,
                    latitude=record["last_lat"],
                    longitude=record["last_lon"],
                    hae=float(record["last_alt"]),
                    course=float(record["course"]),
                    speed=float(record["speed"]),
                    timestamp=datetime.datetime.now(datetime.timezone.utc),
                )
                self.db.session.add(point)
                self.db.session.commit()
                self.socketio.emit("point", point.to_json(), namespace=NAMESPACE)

            return self.cot(pb, from_id, to_id, portnum)
        except Exception as e:  # noqa: BLE001
            self.logger.error("Failed to handle Meshtastic position from {}: {}".format(from_id, e))
            return None

    def node_info(self, pb, from_id, to_id, portnum):
        record = self._record(from_id)
        record["hw_model"] = mesh_pb2.HardwareModel.Name(pb.hw_model)
        record["long_name"] = pb.long_name or "Meshtastic {}".format(from_id[-4:])
        record["short_name"] = pb.short_name or from_id[-4:]
        record["macaddr"] = base64.b64encode(pb.macaddr).decode("ascii") if pb.macaddr else ""
        self.insert_or_update_eud(from_id, from_id, update_if_exists=True)
        return self.cot(pb, from_id, to_id, portnum)

    def _chatroom_for(self, to_id):
        if to_id == "all":
            return "All Chat Rooms"
        for record in list(self.meshtastic_devices.values()):
            if record.get("uid") and _same_node(record.get("meshtastic_id"), to_id):
                return record["uid"]
        return "All Chat Rooms"

    def _geochat(self, event, from_uid, callsign, chatroom, chat_id, to, message):
        detail = event.find("detail")
        message_id = str(uuid.uuid4())
        event.set("uid", "GeoChat.{}.{}.{}".format(from_uid, chat_id, message_id))
        chat = ET.SubElement(
            detail,
            "__chat",
            {
                "chatroom": chatroom,
                "groupOwner": "false",
                "id": chat_id,
                "messageId": message_id,
                "parent": "RootContactGroup",
                "senderCallsign": callsign,
            },
        )
        ET.SubElement(chat, "chatgrp", {"id": chat_id, "uid0": from_uid, "uid1": chat_id})
        ET.SubElement(detail, "link", {"relation": "p-p", "type": "a-f-G-U-C", "uid": from_uid})
        remarks = ET.SubElement(
            detail,
            "remarks",
            {
                "source": "BAO.F.ATAK.{}".format(from_uid),
                "time": _cot_time(datetime.datetime.now(datetime.timezone.utc)),
                "to": to,
            },
        )
        remarks.text = message
        return event

    def text_message(self, pb, from_id, to_id, portnum):
        record = self._record(from_id)
        message = bytes(pb).decode("utf-8", errors="replace")
        callsign = record.get("long_name") or from_id
        chatroom = self._chatroom_for(to_id)
        from_uid = record.get("uid") or from_id
        # The point comes from the sender's own record, keyed by its node id
        event = self.cot(pb, from_id, to_id, portnum, how="h-g-i-g-o", cot_type="b-t-f", uid=from_uid)
        return self._geochat(event, from_uid, callsign, chatroom, chatroom, chatroom, message)

    def telemetry(self, pb, from_id, to_id, portnum):
        record = self._record(from_id)
        if pb.HasField("device_metrics"):
            metrics = pb.device_metrics
            record["battery"] = metrics.battery_level
            record["voltage"] = metrics.voltage
            record["uptime"] = metrics.uptime_seconds
        elif pb.HasField("environment_metrics"):
            metrics = pb.environment_metrics
            for field in (
                "temperature",
                "relative_humidity",
                "barometric_pressure",
                "gas_resistance",
                "voltage",
                "current",
                "iaq",
            ):
                record[field] = getattr(metrics, field)
        return None

    def atak_plugin(self, pb, from_id, to_id, portnum):
        record = self._record(from_id)

        if pb.HasField("contact"):
            atak_uid = _unishox(pb.contact.device_callsign)
            record["uid"] = atak_uid
            record["long_name"] = _unishox(pb.contact.callsign)
            record["short_name"] = atak_uid[-4:]
            record["battery"] = pb.status.battery
            if pb.group.team != 0:
                record["team"] = atak_pb2.Team.Name(pb.group.team)
            if pb.group.role != 0:
                record["role"] = atak_pb2.MemberRole.Name(pb.group.role)
            self.insert_or_update_eud(atak_uid, from_id, update_if_exists=True)

        if pb.HasField("status"):
            record["battery"] = pb.status.battery

        if pb.HasField("pli"):
            record["last_lat"] = pb.pli.latitude_i * 1e-7
            record["last_lon"] = pb.pli.longitude_i * 1e-7
            record["last_alt"] = pb.pli.altitude
            record["course"] = pb.pli.course or "0.0"
            record["speed"] = pb.pli.speed or "0.0"
            return self.cot(pb, from_id, to_id, portnum)

        if pb.HasField("chat"):
            to = _unishox(pb.chat.to)
            message = _unishox(pb.chat.message)
            from_uid = record.get("uid") or from_id
            callsign = record.get("long_name") or from_id
            event = self.cot(pb, from_id, to_id, portnum, how="h-g-i-g-o", cot_type="b-t-f", uid=from_uid)
            return self._geochat(event, from_uid, callsign, "All Chat Rooms", to, to, message)

        return None

    def protobuf_to_cot(self, pb, from_id, to_id, portnum, meshtastic_id=None):
        if portnum == PortNum.MAP_REPORT_APP or (portnum == PortNum.POSITION_APP and pb.latitude_i):
            return self.position(pb, from_id, to_id, portnum)
        if portnum == PortNum.NODEINFO_APP:
            return self.node_info(pb, from_id, to_id, portnum)
        if portnum == PortNum.TEXT_MESSAGE_APP:
            return self.text_message(pb, from_id, to_id, portnum)
        if portnum == PortNum.ATAK_PLUGIN:
            return self.atak_plugin(pb, from_id, to_id, portnum)
        if portnum == PortNum.TELEMETRY_APP:
            return self.telemetry(pb, from_id, to_id, portnum)
        return None

    # ------------------------------------------------------------------ publishing

    def _publish(self, exchange, routing_key, body, ttl=True):
        kwargs = {"properties": self._ttl_properties()} if ttl else {}
        self.rabbit_channel.basic_publish(exchange=exchange, routing_key=routing_key, body=body, **kwargs)

    def _convert_and_publish(self, pb, from_id, to_id, portnum):
        event = self.protobuf_to_cot(pb, from_id, to_id, portnum, from_id)
        if event is None:
            return

        record = self._record(from_id)
        body = json.dumps({"uid": record.get("uid") or from_id, "cot": ET.tostring(event, encoding="unicode")})
        group_key = "{}.OUT".format(self._config.get("RAVEN_MESHTASTIC_GROUP"))

        try:
            self._publish("firehose", "", body)

            if portnum == PortNum.TEXT_MESSAGE_APP:
                if to_id == "all":
                    self._publish("groups", group_key, body)
                else:
                    for device in list(self.meshtastic_devices.values()):
                        if device.get("uid") and _same_node(device.get("meshtastic_id"), to_id):
                            self._publish("dms", device["uid"], body)
            elif portnum == PortNum.ATAK_PLUGIN and pb.HasField("chat"):
                to = _unishox(pb.chat.to)
                if to in self.meshtastic_devices:
                    self._publish("dms", to, body)
                else:
                    self._publish("groups", group_key, body)
            else:
                self._publish("groups", group_key, body)
        except Exception as e:  # noqa: BLE001
            self.logger.error("Failed to publish Meshtastic CoT from {}: {}".format(from_id, e))

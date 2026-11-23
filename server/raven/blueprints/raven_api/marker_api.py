"""
Map markers from the web map: list, create/change, delete.

A new or changed marker becomes a CoT event sent to the CoT parser, the firehose and the
caller's groups; deleting one sends a TAK delete CoT the same way.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import Element, SubElement, tostring

import bleach
import pika
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from flask_security import auth_required, current_user
from sqlalchemy.exc import IntegrityError

from raven.blueprints.raven_api.api import paginate, route_cot, search
from raven.extensions import db, logger, socketio
from raven.functions import (
    cot_type_to_2525c,
    generate_delete_cot,
    get_affiliation,
    get_battle_dimension,
    iso8601_string_from_datetime,
)
from raven.models.CoT import CoT
from raven.models.Marker import Marker
from raven.models.Point import Point
from raven.ownership import can_modify_device_item

marker_api_blueprint = Blueprint("marker_api_blueprint", __name__)

# What an existing marker takes over from a re-sent one (parent_uid is kept)
MARKER_FIELDS = (
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

DEFAULT_ACCURACY = 9999999.0


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def _publish(xml: str) -> None:
    """Sends a CoT to the CoT parser, the firehose and the caller's groups."""
    credentials = pika.PlainCredentials(
        app.config.get("RAVEN_RABBITMQ_USERNAME"), app.config.get("RAVEN_RABBITMQ_PASSWORD")
    )
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=app.config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"), credentials=credentials)
    )
    try:
        channel = connection.channel()
        body = json.dumps({"cot": xml, "uid": app.config.get("RAVEN_NODE_ID")})
        properties = pika.BasicProperties(expiration=app.config.get("RAVEN_RABBITMQ_TTL"))
        channel.basic_publish(exchange="cot_parser", routing_key="cot_parser", body=body, properties=properties)
        channel.basic_publish(exchange="firehose", routing_key="", body=body, properties=properties)
    finally:
        connection.close()
    route_cot(xml, current_user)


def _optional_float(value):
    return None if value is None else float(value)


@marker_api_blueprint.route("/api/markers")
@auth_required()
def get_markers():
    query = db.session.query(Marker)
    for field in ("uid", "affiliation", "callsign"):
        query = search(query, Marker, field)
    return paginate(query)


@marker_api_blueprint.route("/api/markers", methods=["POST"])
@auth_required()
def add_marker():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    if "latitude" not in body or "longitude" not in body:
        return _error(gettext("Please provide a latitude and longitude"))
    try:
        latitude = float(body["latitude"])
        longitude = float(body["longitude"])
    except (TypeError, ValueError) as e:
        return _error(gettext("Failed to parse lat/lon: %(e)s", e=str(e)))
    if not -90 <= latitude <= 90:
        return _error(gettext("Invalid latitude: %(latitude)s", latitude=latitude))
    if not -180 <= longitude <= 180:
        return _error(gettext("Invalid longitude: %(longitude)s", longitude=longitude))

    if "uid" not in body:
        return _error(gettext("Please provide a UID"))
    if "name" not in body:
        return _error(gettext("Please provide a name"))

    uid = body["uid"]
    try:
        if not isinstance(uid, str):
            raise ValueError(uid)
        uuid.UUID(uid)
    except ValueError:
        return _error(gettext("Invalid UID. UIDs need to be in UUID4 format"))
    uid = bleach.clean(uid)

    existing = db.session.query(Marker).filter_by(uid=uid).first()
    if existing is not None and not can_modify_device_item(current_user, existing.parent_uid):
        return _error(
            gettext("Only an administrator or the owner of the device that sent it can change this marker"), 403
        )

    cot_type = body.get("type", "a-u-G")
    affiliation = battle_dimension = None
    if isinstance(cot_type, str) and len(cot_type.split("-")) >= 3:
        affiliation = get_affiliation(cot_type)
        battle_dimension = get_battle_dimension(cot_type)
    if not affiliation or not battle_dimension:
        return _error(gettext("Invalid type: %(cot_type)s", cot_type=bleach.clean(str(cot_type))))

    try:
        callsign = bleach.clean(str(body["name"]))
        location_source = bleach.clean(str(body.get("location_source", "")))
        course = float(body.get("course", 0))
        azimuth = float(body.get("azimuth", 0))
        speed = float(body.get("speed", 0))
        battery = _optional_float(body.get("battery"))
        fov = _optional_float(body.get("fov"))
        ce = float(body.get("ce", DEFAULT_ACCURACY))
        hae = float(body.get("hae", DEFAULT_ACCURACY))
        le = float(body.get("le", DEFAULT_ACCURACY))
        mil_std_2525c = cot_type_to_2525c(cot_type)

        now = datetime.now(timezone.utc)
        stale = now + timedelta(days=1)
        event = Element(
            "event",
            {
                "type": cot_type,
                "version": "2.0",
                "how": "m-g",
                "uid": uid,
                "time": iso8601_string_from_datetime(now),
                "start": iso8601_string_from_datetime(now),
                "stale": iso8601_string_from_datetime(stale),
            },
        )
        SubElement(
            event,
            "point",
            {"ce": str(ce), "hae": str(hae), "le": str(le), "lat": str(latitude), "lon": str(longitude)},
        )
        detail = SubElement(event, "detail", {"uid": uid})
        if "archive" in body:
            SubElement(detail, "archive")
        SubElement(detail, "contact", {"callsign": callsign})
        SubElement(detail, "track", {"course": str(course), "speed": str(speed)})
        if azimuth or fov:
            SubElement(
                detail,
                "sensor",
                {
                    "azimuth": str(azimuth),
                    "fov": str(fov),
                    "fovBlue": "1.0",
                    "fovGreen": "1.0",
                    "fovRed": "1.0",
                    "range": "100.0",
                },
            )
        xml = tostring(event).decode()

        _publish(xml)

        cot = CoT()
        cot.how = "m-g"
        cot.type = cot_type
        cot.uid = uid
        cot.timestamp = now
        cot.start = now
        cot.stale = stale
        cot.xml = xml
        cot.sender_callsign = current_user.username
        db.session.add(cot)
        db.session.flush()

        point = Point()
        point.uid = str(uuid.uuid4())
        point.device_uid = None
        point.latitude = latitude
        point.longitude = longitude
        point.ce = ce
        point.hae = hae
        point.le = le
        point.course = course
        point.speed = speed
        point.azimuth = azimuth
        point.fov = fov
        point.battery = battery
        point.location_source = location_source
        point.timestamp = now
        point.cot_id = cot.id
        db.session.add(point)
        db.session.flush()

        values = {
            "uid": uid,
            "affiliation": affiliation,
            "battle_dimension": battle_dimension,
            "mil_std_2525c": mil_std_2525c,
            "callsign": callsign,
        }

        if existing is None:
            marker = Marker()
            for key, value in values.items():
                setattr(marker, key, value)
            marker.point_id = point.id
            marker.cot_id = cot.id
            try:
                with db.session.begin_nested():
                    db.session.add(marker)
            except IntegrityError:
                # Created meanwhile: change that one instead
                existing = db.session.query(Marker).filter_by(uid=uid).one()
                if not can_modify_device_item(current_user, existing.parent_uid):
                    db.session.rollback()
                    return _error(
                        gettext("Only an administrator or the owner of the device that sent it can change this marker"),
                        403,
                    )

        if existing is not None:
            for field in MARKER_FIELDS:
                setattr(existing, field, values.get(field))
            existing.point_id = point.id
            existing.cot_id = cot.id

        db.session.commit()

        marker = db.session.query(Marker).filter_by(uid=uid).one()
        socketio.emit("marker", marker.to_json(), namespace="/socket.io")
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to add marker {uid}: {e}")
        return _error(gettext("Failed to parse data: %(e)s", e=str(e)))


@marker_api_blueprint.route("/api/markers", methods=["DELETE"])
@auth_required()
def delete_marker():
    uid = request.args.get("uid")
    if not uid:
        return _error(gettext("Please provide the UID of the marker to delete"))
    uid = bleach.clean(uid)

    marker = db.session.query(Marker).filter_by(uid=uid).first()
    if marker is None:
        return _error(gettext("Unknown UID"), 404)
    if not can_modify_device_item(current_user, marker.parent_uid):
        return _error(
            gettext("Only an administrator or the owner of the device that sent it can delete this marker"), 403
        )

    event = generate_delete_cot(uid, marker.cot.type)
    event.set("stale", iso8601_string_from_datetime(datetime.now(timezone.utc) + timedelta(minutes=10)))
    _publish(tostring(event).decode())

    db.session.delete(marker)
    db.session.commit()
    return jsonify({"success": True})

"""CITrap (incident report) API: report zips with a report.xml are uploaded, searched, fetched,
subscribed to (live notifications over RabbitMQ) and deleted.

MAX_REPORT_XML_BYTES and gettext are read through this module at call time (tests replace them).
"""

import datetime
import hashlib
import importlib
import io
import json
import os
import re
import uuid
import zipfile

import bleach
import pika
from bs4 import BeautifulSoup
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from sqlalchemy import select
from werkzeug.utils import secure_filename

from raven.extensions import db, logger
from raven.functions import datetime_from_iso8601_string
from raven.models.CITrap import CITrap
from raven.models.EUD import EUD
from raven.models.Point import Point

citrap_api_blueprint = Blueprint("citrap_api_blueprint", __name__)

MAX_REPORT_XML_BYTES = 2 * 1024 * 1024

_POINT_RE = re.compile(r"POINT\s*\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)")


class _TooLarge(Exception):
    pass


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


def _report_file_name(report) -> str:
    """The stored file name of a report: <title>-<id>.zip, or <id>.zip without a title."""
    title = secure_filename(report.attrs.get("title") or "")[:100]
    report_id = secure_filename(report.attrs.get("id") or "")[:100] or uuid.uuid4().hex
    return f"{title}-{report_id}.zip" if title else f"{report_id}.zip"


def _rabbitmq_channel():
    credentials = pika.PlainCredentials(
        app.config.get("RAVEN_RABBITMQ_USERNAME"), app.config.get("RAVEN_RABBITMQ_PASSWORD")
    )
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=app.config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"), credentials=credentials)
    )
    return connection, connection.channel()


def _client_uid():
    client_uid = request.args.get("clientUid") or request.args.get("uid")
    return bleach.clean(client_uid) if client_uid else ""


@citrap_api_blueprint.route("/Marti/api/missions/citrap/subscription", methods=["PUT"], strict_slashes=False)
def citrap_subscription():
    client_uid = _client_uid()
    if not client_uid:
        return _error(gettext("clientUid not found"), 400)

    try:
        connection, channel = _rabbitmq_channel()
        channel.queue_declare(queue=client_uid)
        channel.queue_bind(exchange="missions", queue=client_uid, routing_key="missions.citrap")
        connection.close()
    except BaseException as e:
        logger.error(f"Failed to subscribe {client_uid} to CITrap reports: {e}")
        return _error(gettext("Failed to subscribe"), 500)

    return "", 201


@citrap_api_blueprint.route("/Marti/api/citrap", methods=["GET"], strict_slashes=False)
def get_reports():
    query = select(CITrap)

    report_type = request.args.get("type")
    if report_type:
        query = query.filter(CITrap.type == bleach.clean(report_type))
    callsign = request.args.get("callsign")
    if callsign:
        query = query.filter(CITrap.user_callsign == bleach.clean(callsign))

    try:
        query = query.limit(int(request.args.get("maxReportCount")))
    except (TypeError, ValueError):
        pass

    return jsonify([report.to_json() for report in db.session.execute(query).scalars()])


def _read_report_xml(data: bytes) -> str:
    """The report.xml text of an uploaded report zip. Raises ValueError (bad package) or _TooLarge."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError(gettext("Invalid report package"))

    with archive:
        member = next((info for info in archive.infolist() if info.filename.endswith("report.xml")), None)
        if member is None:
            raise ValueError(gettext("report.xml not found"))

        limit = MAX_REPORT_XML_BYTES
        if member.file_size > limit:
            raise _TooLarge()
        try:
            with archive.open(member) as f:
                raw = f.read(limit + 1)
        except BaseException:
            raise ValueError(gettext("Invalid report file"))
        if len(raw) > limit:
            raise _TooLarge()

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(gettext("Invalid report file"))


def _location(text):
    """(latitude, longitude) of a WKT POINT (lon lat); 0, 0 when missing or unreadable"""
    match = _POINT_RE.search(text or "")
    if not match:
        return 0.0, 0.0
    try:
        return float(match.group(2)), float(match.group(1))
    except ValueError:
        return 0.0, 0.0


def _report_time(value):
    """Report times are stored as naive UTC"""
    parsed = datetime_from_iso8601_string(value)
    return parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)


@citrap_api_blueprint.route("/Marti/api/citrap", methods=["POST"], strict_slashes=False)
def post_report():
    client_uid = _client_uid()
    if not client_uid:
        return _error(gettext("clientUid not found"), 400)

    reports_folder = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "reports")
    os.makedirs(reports_folder, exist_ok=True)

    data = request.get_data()
    try:
        report_xml = _read_report_xml(data)
    except _TooLarge:
        return _error(gettext("Report file is too large"), 413)
    except ValueError as e:
        return _error(str(e), 400)

    report = BeautifulSoup(report_xml, "xml").find("report")
    if report is None:
        return _error(gettext("Invalid report file"), 400)

    file_name = _report_file_name(report)
    with open(os.path.join(reports_folder, file_name), "wb") as f:
        f.write(data)

    report_id = report.attrs.get("id") or str(uuid.uuid4())
    file_hash = hashlib.sha256(data).hexdigest()
    latitude, longitude = _location(report.attrs.get("location"))
    timestamp = _report_time(report.attrs.get("dateTime"))

    # A re-submission (same id, or the same bytes under another id) updates the stored report
    citrap = db.session.get(CITrap, report_id)
    if citrap is None:
        citrap = db.session.execute(select(CITrap).filter_by(hash=file_hash)).scalar()
        if citrap is not None:
            report_id = citrap.id
    new_report = citrap is None

    # A device that never connected has no euds row; its report is kept without one
    known_device = db.session.execute(select(EUD.id).filter_by(uid=client_uid)).first() is not None

    try:
        point = citrap.point if citrap is not None else None
        if point is None:
            point = Point()
            point.uid = report.attrs.get("id") or str(uuid.uuid4())
            db.session.add(point)
        point.device_uid = client_uid if known_device else None
        point.latitude = latitude
        point.longitude = longitude
        point.timestamp = timestamp
        db.session.flush()

        if new_report:
            citrap = CITrap()
            citrap.id = report_id
            db.session.add(citrap)

        visibility = report.attrs.get("visibilityStatus")
        citrap.type = report.attrs.get("type")
        citrap.title = report.attrs.get("title")
        citrap.visible = True if visibility is None else visibility.lower() == "true"
        citrap.delimiter = report.attrs.get("delimiter")
        citrap.user_callsign = report.attrs.get("userCallsign")
        citrap.user_description = report.attrs.get("userDescription")
        citrap.date_time = timestamp
        citrap.date_time_description = report.attrs.get("dateTimeDescription")
        citrap.point_id = point.id
        citrap.location_description = report.attrs.get("locationDescription")
        citrap.tags = report.attrs.get("tags")
        citrap.event_scale = report.attrs.get("eventScale")
        citrap.scale_description = report.attrs.get("scaleDescription")
        citrap.importance = report.attrs.get("importance")
        citrap.status = report.attrs.get("status")
        citrap.file_name = file_name
        citrap.hash = file_hash
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to add CITrap report {report_id}: {e}")
        return _error(gettext("Failed to add report"), 500)

    try:
        connection, channel = _rabbitmq_channel()
        channel.basic_publish(
            exchange="missions",
            routing_key="missions.citrap",
            body=json.dumps({"uid": client_uid, "cot": report_xml}),
            properties=pika.BasicProperties(expiration=app.config.get("RAVEN_RABBITMQ_TTL")),
        )
        connection.close()
    except BaseException as e:
        logger.error(f"Failed to publish CITrap report {report_id}: {e}")

    return jsonify({"id": report_id}), 201


@citrap_api_blueprint.route("/Marti/api/citrap/<report_id>", methods=["GET", "PUT"], strict_slashes=False)
def get_report(report_id):
    report = db.session.get(CITrap, bleach.clean(report_id))
    if report is None:
        return _error(gettext("Report not found"), 404)
    return jsonify(report.to_json())


def _authenticated() -> bool:
    from flask_login import current_user

    try:
        if current_user and current_user.is_authenticated:
            return True
    except BaseException:
        pass

    marti_api_module = importlib.import_module("raven.blueprints.marti_api.marti_api")
    try:
        return bool(marti_api_module.verify_client_cert())
    except BaseException:
        return False


@citrap_api_blueprint.route("/Marti/api/citrap/<report_id>", methods=["DELETE"], strict_slashes=False)
def delete_report(report_id):
    if not _authenticated():
        return _error(gettext("Authentication required"), 401)

    report = db.session.get(CITrap, bleach.clean(report_id))
    if report is None:
        return _error(gettext("Report not found"), 404)

    db.session.delete(report)
    db.session.commit()
    return jsonify({"success": True})

"""Client certificate checks shared by the Marti API, plus the client endpoint list, the server
version probe and the KML/KMZ track export.

verify_client_cert() is looked up through this module at call time (raven/marti_auth.py and the
other Marti modules), so tests can replace it here.
"""

import os
import uuid
from urllib.parse import unquote, urlparse

import simplekml
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request, send_from_directory
from flask_babel import gettext
from OpenSSL import crypto
from sqlalchemy import select
from werkzeug.utils import secure_filename

import raven
from raven.cert_revocation import is_revoked
from raven.extensions import db, logger
from raven.functions import datetime_from_iso8601_string, iso8601_string_from_datetime
from raven.marti_auth import marti_caller_required
from raven.models.EUD import EUD
from raven.models.Point import Point

marti_api = Blueprint("marti_api", __name__)

_ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def verify_client_cert():
    """The client certificate nginx verified and forwarded, if it chains to our CA and isn't revoked.

    :returns: the pyOpenSSL X509 object, or False. A malformed header raises.
    """
    header_value = request.headers.get(app.config.get("RAVEN_SSL_CERT_HEADER", "X-Ssl-Cert"))
    if not header_value:
        return False

    cert = crypto.load_certificate(crypto.FILETYPE_PEM, unquote(header_value).encode())

    ca_folder = app.config.get("RAVEN_CA_FOLDER")
    with open(os.path.join(ca_folder, "ca.pem"), "rb") as f:
        ca_cert = crypto.load_certificate(crypto.FILETYPE_PEM, f.read())

    store = crypto.X509Store()
    store.add_cert(ca_cert)
    try:
        crypto.X509StoreContext(store, cert).verify_certificate()
    except crypto.X509StoreContextError as e:
        logger.warning(f"Client certificate failed verification: {e}")
        return False

    if is_revoked(ca_folder, cert.get_serial_number()):
        logger.warning(f"Refused revoked client certificate {cert.get_serial_number():X}")
        return False

    return cert


def track_export_filename(uid: str, extension: str) -> str:
    """A file name for a track export that can't leave the upload folder."""
    safe = secure_filename(uid or "")
    if not safe:
        safe = uuid.uuid4().hex
    return f"{safe}.{extension}"


def _client_endpoint(eud) -> dict:
    owner = eud.user
    return {
        "uid": eud.uid,
        "callsign": eud.callsign,
        "username": owner.username if owner else "anonymous",
        "lastStatus": eud.last_status,
        "lastEventTime": iso8601_string_from_datetime(eud.last_event_time),
    }


@marti_api.route("/Marti/api/clientEndPoints", methods=["GET"])
@marti_caller_required
def client_end_points():
    # Only devices that have actually reported in (a callsign and a last event) are listed
    data = [
        _client_endpoint(eud)
        for eud in db.session.execute(select(EUD)).scalars()
        if eud.callsign and eud.last_event_time is not None
    ]

    return jsonify(
        {
            "version": 3,
            "type": "com.bbn.marti.remote.ClientEndpoint",
            "data": data,
            "nodeId": app.config.get("RAVEN_NODE_ID"),
        }
    )


@marti_api.route("/Marti/api/version/config", methods=["GET"])
def marti_config():
    return jsonify(
        {
            "version": "3",
            "type": "ServerConfig",
            "data": {
                "version": raven.__version__,
                "api": "3",
                "hostname": urlparse(request.url_root).hostname,
            },
            "nodeId": app.config.get("RAVEN_NODE_ID"),
        }
    )


@marti_api.route("/Marti/ExportMissionKML", methods=["GET"])
@marti_caller_required
def export_mission_kml():
    uid = request.args.get("uid")
    eud = db.session.execute(select(EUD).filter_by(uid=uid)).scalar() if uid else None
    if not eud:
        return jsonify({"success": False, "error": gettext("No such UID: %(uid)s", uid=uid)}), 400

    try:
        start_time = request.args.get("startTime")
        end_time = request.args.get("endTime")
        kmz = request.args.get("format") == "kmz"

        query = select(Point).filter(Point.device_uid == uid)
        if start_time:
            query = query.filter(Point.timestamp >= _naive(datetime_from_iso8601_string(start_time)))
        if end_time:
            query = query.filter(Point.timestamp <= _naive(datetime_from_iso8601_string(end_time)))
        points = list(db.session.execute(query).scalars())

        kml = simplekml.Kml()
        kml.document.name = uid

        icon_name = f"team_{eud.team.name.lower().replace(' ', '')}.png"
        style = simplekml.Style()
        style.iconstyle.scale = 1.0
        style.iconstyle.heading = 0.0
        icon_path = os.path.join(_ICON_FOLDER, icon_name)
        if kmz and os.path.exists(icon_path):
            style.iconstyle.icon.href = kml.addfile(icon_path)
        else:
            style.iconstyle.icon.href = f"files/{icon_name}"

        multi_track = kml.newgxmultitrack(name=eud.callsign, gxinterpolate=0)
        multi_track.style = style
        track = multi_track.newgxtrack(name=uid)
        track.altitudemode = simplekml.AltitudeMode.clamptoground
        track.newwhen([iso8601_string_from_datetime(p.timestamp) for p in points])
        track.newgxcoord([(p.longitude, p.latitude) for p in points])

        extension = "kmz" if kmz else "kml"
        filename = track_export_filename(uid, extension)
        upload_folder = app.config.get("UPLOAD_FOLDER")
        path = os.path.join(upload_folder, filename)
        if kmz:
            kml.savekmz(path)
        else:
            kml.save(path)

        return send_from_directory(upload_folder, filename, as_attachment=True, download_name=filename)
    except BaseException as e:
        logger.error(f"Failed to export the track of {uid}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _naive(value):
    """Point timestamps are stored as naive UTC"""
    return value.replace(tzinfo=None) if value is not None else None

"""
General web API: health and config probes, dashboard status, per-user certificate packages, the
current user, Remote Clear plugin assets, paginated read APIs (CoT, alerts, points, EUDs, icons),
the map snapshot, the RabbitMQ HTTP auth backend callback, the public truststore and the iTAK QR
string.

Also home of the helpers other blueprints import: search, paginate, change_config_setting and
route_cot.
"""

import datetime
import hashlib
import io
import json
import os
import platform
import shutil
import time
import traceback
from urllib.parse import urlparse

import bleach
import pika
import psutil
import sqlalchemy
import yaml
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request, send_file
from flask_babel import gettext
from flask_security import auth_required, current_user
from flask_security.utils import verify_password
from sqlalchemy import String, Text, Unicode, UnicodeText

import raven
from raven import cert_revocation
from raven.admin_view import is_admin_viewer, is_full_admin, viewer_areas
from raven.audit import record_audit_event
from raven.cert_validity import resolve_cert_days
from raven.config_file import write_config_yml
from raven.extensions import db, logger
from raven.models.Alert import Alert
from raven.models.CasEvac import CasEvac
from raven.models.Certificate import Certificate
from raven.models.CoT import CoT
from raven.models.DataPackage import DataPackage
from raven.models.EUD import EUD
from raven.models.Icon import Icon
from raven.models.Marker import Marker
from raven.models.Point import Point
from raven.models.RBLine import RBLine

api_blueprint = Blueprint("api_blueprint", __name__)

_TEXT_TYPES = (String, Text, Unicode, UnicodeText)
_UNKNOWN_OS_RELEASE = {"NAME": None, "PRETTY_NAME": None, "VERSION": None, "VERSION_CODENAME": None}


# ------------------------------------------------------------------------------------- helpers


def search(query, model, field):
    """Adds an exact-match filter on model.<field> when the request's query string has a non-empty
    value for it."""
    value = request.args.get(field)
    if value:
        query = query.filter(getattr(model, field) == bleach.clean(value))
    return query


def _sort_column(model, sort_by):
    """The column to order by, or None if sort_by isn't a column attribute of model."""
    attribute = getattr(model, sort_by, None)
    column_type = getattr(attribute, "type", None)
    if attribute is None or column_type is None:
        return None
    if isinstance(column_type, _TEXT_TYPES):
        return sqlalchemy.func.lower(attribute)
    return attribute


def paginate(query, model=None):
    """One page of query as the standard listing JSON. Sorting (sort_by/sort_direction) only when
    model is given; it comes after any ordering the caller already applied."""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
    except (TypeError, ValueError):
        return (
            {"success": False, "error": "Invalid page or per_page number"},
            400,
            {"Content-Type": "application/json"},
        )

    sort_by = request.args.get("sort_by")
    if model is not None and sort_by:
        column = _sort_column(model, sort_by)
        if column is None:
            return (
                jsonify({"success": False, "error": gettext("Invalid sort column: %(sort_by)s", sort_by=sort_by)}),
                400,
            )
        if request.args.get("sort_direction") == "desc":
            column = column.desc()
        else:
            column = column.asc()
        query = query.order_by(column)

    if isinstance(query, sqlalchemy.sql.Select):
        rows = db.paginate(query, page=page, per_page=per_page)
    else:
        rows = query.paginate(page=page, per_page=per_page)

    results = []
    for row in rows.items:
        serialised = row.to_json()
        if serialised not in results:
            results.append(serialised)

    return jsonify(
        {
            "results": results,
            "total_pages": rows.pages,
            "current_page": page,
            "per_page": per_page,
            "total": rows.total,
        }
    )


def change_config_setting(setting, value):
    """Sets one top-level key in config.yml. Never raises. The value is never logged."""
    try:
        path = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "config.yml")
        with open(path) as f:
            config = yaml.safe_load(f) or {}
        config[setting] = value
        write_config_yml(path, config)
    except BaseException as e:
        logger.error(f"Failed to change the {setting} setting in config.yml: {e.__class__.__name__}")
        logger.debug(traceback.format_exc())


def route_cot(event: str, user):
    """Publishes a CoT to every group the user may send into (or __ANON__ if none)."""
    from raven.models.Group import Group
    from raven.models.GroupUser import GroupUser

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=app.config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"),
            credentials=pika.PlainCredentials(
                app.config.get("RAVEN_RABBITMQ_USERNAME"), app.config.get("RAVEN_RABBITMQ_PASSWORD")
            ),
        )
    )
    channel = connection.channel()

    group_names = (
        db.session.execute(
            sqlalchemy.select(Group.name)
            .join(GroupUser, GroupUser.group_id == Group.id)
            .where(GroupUser.user_id == user.id, GroupUser.direction == "IN", GroupUser.enabled.is_(True))
        )
        .scalars()
        .all()
    )
    routing_keys = [f"{name}.OUT" for name in group_names] or ["__ANON__.OUT"]

    body = json.dumps({"uid": app.config.get("RAVEN_NODE_ID"), "cot": str(event)})
    properties = pika.BasicProperties(expiration=str(app.config.get("RAVEN_RABBITMQ_TTL", "86400000")))
    for routing_key in routing_keys:
        channel.basic_publish(exchange="groups", routing_key=routing_key, body=body, properties=properties)

    channel.close()
    connection.close()


def _request_hostname():
    return urlparse(request.url_root).hostname


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


# ------------------------------------------------------------------------------- public probes


@api_blueprint.route("/files/api/config", methods=["GET"])
def cloudtak_config():
    return jsonify({"uploadSizeLimit": 400})


@api_blueprint.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"})


@api_blueprint.route("/api/turnstile", methods=["GET"])
def turnstile_config():
    return jsonify(
        {
            "enabled": bool(app.config.get("RAVEN_TURNSTILE_ENABLE")),
            "site_key": app.config.get("RAVEN_TURNSTILE_SITE_KEY") or "",
        }
    )


# ------------------------------------------------------------------------------------- status


def _os_release():
    try:
        return platform.freedesktop_os_release()
    except OSError:
        return dict(_UNKNOWN_OS_RELEASE)


@api_blueprint.route("/api/status", methods=["GET"])
@auth_required()
def status():
    now = datetime.datetime.now(datetime.timezone.utc)
    boot_timestamp = psutil.boot_time()
    # Expressed in UTC so the trailing Z is true
    boot_time = datetime.datetime.fromtimestamp(boot_timestamp, tz=datetime.timezone.utc)

    start_time = getattr(app, "start_time", None) or now
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=datetime.timezone.utc)

    cpu = psutil.cpu_times()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    network = psutil.net_io_counters()
    uname = platform.uname()

    online_euds = db.session.query(EUD).filter(EUD.last_status == "Connected").count()

    return jsonify(
        {
            "online_euds": online_euds,
            "system_boot_time": boot_time.strftime("%Y-%m-%d %H:%M:%SZ"),
            "system_uptime": time.time() - boot_timestamp,
            "server_start_time": start_time.strftime("%Y-%m-%d %H:%M:%SZ"),
            "server_uptime": (now - start_time).total_seconds(),
            "cpu_time": {"user": cpu.user, "system": cpu.system, "idle": cpu.idle},
            "cpu_percent": psutil.cpu_percent(),
            "load_avg": list(psutil.getloadavg()),
            "memory": {
                "total": memory.total,
                "available": memory.available,
                "used": memory.used,
                "free": memory.free,
                "percent": memory.percent,
            },
            "disk_usage": {"total": disk.total, "used": disk.used, "free": disk.free, "percent": disk.percent},
            "network": {
                "bytes_sent": network.bytes_sent,
                "bytes_recv": network.bytes_recv,
                "packets_sent": network.packets_sent,
                "packets_recv": network.packets_recv,
            },
            "server_version": raven.__version__,
            "uname": {
                "system": uname.system,
                "node": uname.node,
                "release": uname.release,
                "version": uname.version,
                "machine": uname.machine,
            },
            "os_release": _os_release(),
            "python_version": platform.python_version(),
        }
    )


# -------------------------------------------------------------------------------- certificates


def _sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _issue_certificate_package():
    from raven.blueprints.marti_api.certificate_enrollment_api import enrollment_blocked_response
    from raven.certificate_authority import CertificateAuthority

    body = request.get_json(silent=True) or {}
    if "username" not in body:
        return _error(gettext("Please specify a callsign"), 400)

    requested = bleach.clean(body.get("username"))
    if requested == current_user.username:
        user = current_user._get_current_object()
    else:
        user = app.security.datastore.find_user(username=requested)
    # A name the datastore lookup resolves to the caller's own account is still the caller
    is_self = user is not None and user.id == current_user.id
    if not is_self and not current_user.has_role("administrator"):
        return _error(gettext("You may only generate a certificate for yourself"), 403)

    if not user:
        return _error(gettext("Invalid username: %(username)s", username=requested), 400)
    username = user.username

    uid = body.get("uid") or None
    if uid:
        blocked = enrollment_blocked_response(uid, username, "certificate package")
        if blocked is not None:
            return blocked

    days = resolve_cert_days(user)
    ca_folder = app.config.get("RAVEN_CA_FOLDER")
    user_folder = os.path.join(ca_folder, "certs", username)

    filenames = CertificateAuthority(logger, app).issue_certificate(username, False, days=days) or []

    serial = None
    try:
        with open(os.path.join(user_folder, username + ".pem"), "rb") as f:
            serial = cert_revocation.serial_from_pem(f.read())
    except OSError as e:
        logger.warning(f"Could not read the serial of {username}'s new certificate: {e}")

    file_hash = None
    for filename in filenames:
        package_path = os.path.join(user_folder, filename)
        file_hash = _sha256_of(package_path)

        package = db.session.query(DataPackage).filter_by(filename=filename).first()
        if package is None:
            package = DataPackage()
            package.filename = filename
        package.keywords = None
        package.tool = "private"
        package.creator_uid = uid
        package.submission_time = datetime.datetime.now(datetime.timezone.utc)
        package.mime_type = "application/x-zip-compressed"
        package.size = os.path.getsize(package_path)
        package.hash = file_hash
        package.submission_user = user.id
        try:
            db.session.add(package)
            db.session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            db.session.rollback()
            logger.error(f"Failed to save the certificate package for {username}: {e}")
            return _error(gettext("Failed to save certificate for %(username)s", username=username), 400)

        upload_folder = app.config.get("UPLOAD_FOLDER")
        os.makedirs(upload_folder, exist_ok=True)
        shutil.copyfile(package_path, os.path.join(upload_folder, f"{file_hash}.zip"))

        certificate = Certificate()
        certificate.common_name = username
        certificate.username = username
        # Server-local time, like the rest of this column
        certificate.expiration_date = datetime.datetime.now() + datetime.timedelta(days=days)
        certificate.server_address = _request_hostname()
        certificate.server_port = app.config.get("RAVEN_SSL_STREAMING_PORT")
        certificate.truststore_filename = os.path.join(ca_folder, "truststore-root.p12")
        certificate.user_cert_filename = os.path.join(user_folder, username + ".p12")
        certificate.data_package_id = package.id
        certificate.serial_number = serial
        db.session.add(certificate)
        db.session.commit()

    record_audit_event(
        "certificate",
        "cert_issued",
        username=username,
        actor=current_user.username,
        details="Certificate package {} issued, valid {} days".format(", ".join(filenames), days),
    )

    if body.get("download") and filenames:
        record_audit_event(
            "enrollment",
            "enroll_zip",
            username=username,
            actor=current_user.username,
            details=f"Certificate package {filenames[0]} downloaded from the web UI",
        )
        return send_file(
            os.path.join(user_folder, filenames[0]),
            mimetype="application/zip",
            as_attachment=True,
            download_name=filenames[0],
        )

    return jsonify(
        {
            "success": True,
            "hash": file_hash,
            "filename": filenames[0] if filenames else None,
            "cert_validity_days": days,
        }
    )


@api_blueprint.route("/api/certificate", methods=["GET", "POST"])
@auth_required()
def certificate():
    if request.method == "POST":
        try:
            return _issue_certificate_package()
        except BaseException as e:
            db.session.rollback()
            logger.error(f"Failed to issue a certificate package: {e}")
            logger.debug(traceback.format_exc())
            return _error(str(e), 500)

    query = db.session.query(Certificate)
    query = search(query, Certificate, "callsign")
    query = search(query, Certificate, "username")
    sees_all = is_full_admin(current_user) or (is_admin_viewer(current_user) and "users" in viewer_areas(current_user))
    if not sees_all:
        query = query.filter(sqlalchemy.func.lower(Certificate.username) == current_user.username.lower())
    return paginate(query)


# ------------------------------------------------------------------------------- current user


@api_blueprint.route("/api/me", methods=["GET"])
@auth_required()
def me():
    data = current_user.to_json()
    data["token"] = current_user.get_auth_token()
    return jsonify(data)


# ------------------------------------------------------------------------------- Remote Clear


@api_blueprint.route("/api/plugins/ravenclear/pubkey", methods=["GET"])
@auth_required()
def ravenclear_pubkey():
    from raven import remote_clear

    return jsonify({"pem": remote_clear.public_key_pem(), "der_b64": remote_clear.public_key_der_b64()})


@api_blueprint.route("/api/plugins/ravenclear/apk", methods=["GET"])
@auth_required()
def ravenclear_apk():
    path = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "plugins", "ravenclear.apk")
    if not os.path.isfile(path):
        return _error(
            gettext(
                "The Remote Clear plugin APK has not been uploaded yet. Build and TAK-sign it (see the "
                "plugin BUILD.md), then place it at %(path)s.",
                path=path,
            ),
            404,
        )
    return send_file(path, as_attachment=True, download_name="C4Raven-RemoteClear.apk")


# -------------------------------------------------------------------------------- read APIs


@api_blueprint.route("/api/cot", methods=["GET"])
@auth_required()
def query_cot():
    query = db.session.query(CoT)
    for field in ("how", "type", "sender_callsign", "sender_uid"):
        query = search(query, CoT, field)
    return paginate(query, CoT)


@api_blueprint.route("/api/alerts", methods=["GET"])
@auth_required()
def query_alerts():
    query = db.session.query(Alert)
    for field in ("uid", "sender_uid", "alert_type"):
        query = search(query, Alert, field)
    return paginate(query, Alert)


@api_blueprint.route("/api/point", methods=["GET"])
@auth_required()
def query_points():
    query = db.session.query(Point)
    if request.args.get("uid") or request.args.get("callsign"):
        # Points of the device(s) matching the EUD filters
        query = query.join(EUD, EUD.uid == Point.device_uid)
        query = search(query, EUD, "uid")
        query = search(query, EUD, "callsign")
    return paginate(query, Point)


@api_blueprint.route("/api/eud", methods=["GET"])
@auth_required()
def get_euds():
    if request.args.get("all"):
        euds = (
            db.session.query(EUD)
            .filter(EUD.uid.isnot(None), EUD.uid != "", EUD.callsign.isnot(None), EUD.callsign != "")
            .all()
        )
        return jsonify([{"uid": eud.uid, "callsign": eud.callsign} for eud in euds])

    from raven.models.user import User

    query = db.session.query(EUD)
    query = search(query, EUD, "callsign")
    query = search(query, EUD, "uid")
    if request.args.get("username"):
        query = query.join(User, User.id == EUD.user_id)
        query = search(query, User, "username")
    return paginate(query, EUD)


@api_blueprint.route("/api/icon", methods=["GET"])
@auth_required()
def get_icon():
    query = db.session.query(Icon)
    for field in ("filename", "groupName", "type2525b"):
        query = search(query, Icon, field)
    return paginate(query, Icon)


# ------------------------------------------------------------------------------------ map state


@api_blueprint.route("/api/features", methods=["GET"])
@auth_required()
def features():
    """The optional parts of the web UI this server has turned on."""
    return jsonify(
        {
            "map": bool(app.config.get("RAVEN_MAP_ENABLE", True)),
            "meshtastic": bool(app.config.get("RAVEN_ENABLE_MESHTASTIC", False)),
        }
    )


@api_blueprint.route("/api/map_state", methods=["GET"])
@auth_required()
def get_map_state():
    if not app.config.get("RAVEN_MAP_ENABLE", True):
        return _error(gettext("The map is turned off on this server"), 404)
    try:
        euds = []
        for eud in db.session.query(EUD).all():
            data = eud.to_json()
            last_point = (
                db.session.query(Point)
                .filter(Point.device_uid == eud.uid, Point.uid == eud.uid)
                .order_by(Point.timestamp.desc())
                .first()
            )
            if last_point is not None:
                data["last_point"] = last_point.to_json()
            euds.append(data)

        # The CoT columns hold naive UTC
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

        def live(model):
            rows = db.session.query(model).join(CoT, CoT.id == model.cot_id).filter(CoT.stale >= now).all()
            return [row.to_json() for row in rows]

        return jsonify({"euds": euds, "markers": live(Marker), "rb_lines": live(RBLine), "casevacs": live(CasEvac)})
    except BaseException as e:
        logger.error(f"Failed to build the map state: {e}")
        logger.debug(traceback.format_exc())
        return _error(str(e), 500)


# ----------------------------------------------------------------------------- RabbitMQ auth


def _plain(text):
    return app.response_class(text, status=200, mimetype="text/plain")


def _ldap_rabbitmq_auth(username, password):
    from flask_ldap3_login import AuthenticationResponseStatus

    from raven.blueprints.raven_api.ldap_api import save_user
    from raven.extensions import ldap_manager

    result = ldap_manager.authenticate(username, password)
    if result.status != AuthenticationResponseStatus.success:
        return _plain("deny")
    save_user(result.user_dn, username, result.user_info, result.user_groups)
    admin_group = app.config.get("RAVEN_LDAP_ADMIN_GROUP")
    for group in result.user_groups or []:
        if group.get("cn") == admin_group:
            return _plain("allow administrator")
    return _plain("allow")


@api_blueprint.route("/api/rabbitmq/<path:path>", methods=["POST"])
def rabbitmq_auth(path):
    # Only the broker itself, talking to us directly (not through nginx)
    if "X-Forwarded-For" in request.headers or request.remote_addr != app.config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"):
        return _plain("deny")

    username = bleach.clean(request.form["username"])
    password = request.form.get("password")
    if password is not None:
        password = bleach.clean(password)

    if app.config.get("RAVEN_ENABLE_LDAP"):
        return _ldap_rabbitmq_auth(username, password)

    user = app.security.datastore.find_user(username=username)
    if not user:
        return _plain("deny")
    if password is None:
        # vhost / resource / topic callbacks carry no password
        return _plain("allow")
    if user.active and user.password and verify_password(password, user.password):
        if user.has_role("administrator"):
            return _plain("allow administrator")
        return _plain("allow")
    return _plain("deny")


# ---------------------------------------------------------------------- truststore and iTAK QR


@api_blueprint.route("/api/truststore", methods=["GET"])
def get_truststore():
    from raven.blueprints.raven_api.onboard_api import export_onboard_truststore

    truststore = export_onboard_truststore(
        app.config.get("RAVEN_CA_FOLDER"), app.config.get("RAVEN_CA_PASSWORD"), app.config.get("RAVEN_CA_NAME")
    )
    return send_file(
        io.BytesIO(truststore),
        mimetype="application/x-pkcs12",
        as_attachment=True,
        download_name=f"truststore_root_{_request_hostname()}.p12",
    )


@api_blueprint.route("/api/itak_qr_string", methods=["GET"])
@auth_required()
def get_settings():
    host = _request_hostname()
    port = app.config.get("RAVEN_SSL_STREAMING_PORT")
    return app.response_class(f"Raven_{host},{host},{port},SSL", status=200, mimetype="text/plain")

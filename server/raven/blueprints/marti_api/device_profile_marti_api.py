"""TAK device profiles: the data packages a device gets right after enrolling (8446, Basic auth),
each time it connects (8443, client cert) and for a tool on request.

The settings and files come from the device profile store (raven/device_profiles.py) on top of
built-in server entries, the CA truststore, map sources, plugins and data packages flagged to
install on enrollment/connection.

verify_client_cert, export_onboard_truststore, remote_clear, basic_auth, file_limits and
_zip_spool are looked up through this module when a profile is built, so tests can replace them.
"""

import datetime
import os
import tempfile
import uuid
import zipfile
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, SubElement, tostring

from flask import Blueprint
from flask import current_app as app
from flask import request, send_file
from sqlalchemy import select

from raven import remote_clear
from raven.blueprints.marti_api.certificate_enrollment_api import basic_auth
from raven.blueprints.marti_api.marti_api import verify_client_cert
from raven.blueprints.raven_api.onboard_api import export_onboard_truststore
from raven.device_profiles import (
    BOOLEAN,
    SERVER_ZIP_FOLDER,
    STRING,
    file_limits,
    resolve_files,
    resolve_settings,
    username_for_eud,
    validate_tool,
)
from raven.extensions import db, ldap_manager, logger
from raven.group_isolation import filter_by_groups
from raven.models.DataPackage import DataPackage
from raven.models.DeviceProfileSet import DeviceProfileSet
from raven.models.Packages import Packages

device_profile_marti_api_blueprint = Blueprint("device_profile_marti_api_blueprint", __name__)

PROTECTED_KEYS = {remote_clear.SERVER_PUBKEY_PREF_KEY, "updateServerCaLocation", "updateServerCaPassword"}

ZIP_SPOOL_BYTES = 8 * 1024 * 1024

_MAPS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "maps")
_PREF_PATH = f"{SERVER_ZIP_FOLDER}/preference.pref"
_TRUSTSTORE_PATH = f"{SERVER_ZIP_FOLDER}/truststore-root.p12"
_MANIFEST_PATH = "MANIFEST/manifest.xml"


def _zip_spool():
    """Where a profile zip is built: in memory up to ZIP_SPOOL_BYTES, then on disk. Deleted on close."""
    return tempfile.SpooledTemporaryFile(max_size=ZIP_SPOOL_BYTES, mode="w+b")


def _hostname():
    return urlparse(request.url_root).hostname


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _request_uid():
    return request.args.get("clientUid") or request.args.get("clientUID")


def _sync_secago() -> int:
    try:
        return int(request.args.get("syncSecago", -1))
    except (TypeError, ValueError):
        return -1


# --- preference.pref ---


def _new_preferences():
    root = Element("preferences")
    pref = SubElement(root, "preference", {"version": "1", "name": "com.atakmap.app_preferences"})
    return root, pref


def _add_entry(pref, key, value_class, value):
    entry = SubElement(pref, "entry", {"key": key, "class": value_class})
    entry.text = value
    return entry


def get_ldap_attributes(pref):
    """Adds the LDAP-derived entries (team colour, role, callsign, prefixed preferences) of the
    client cert's user."""
    if not app.config.get("RAVEN_ENABLE_LDAP"):
        return
    cert = verify_client_cert()
    if not cert:
        return

    user_info = ldap_manager.get_user_info_for_username(cert.get_subject().commonName)
    if not user_info:
        return

    color = str(app.config.get("RAVEN_LDAP_COLOR_ATTRIBUTE") or "").lower()
    role = str(app.config.get("RAVEN_LDAP_ROLE_ATTRIBUTE") or "").lower()
    callsign = str(app.config.get("RAVEN_LDAP_CALLSIGN_ATTRIBUTE") or "").lower()
    prefix = str(app.config.get("RAVEN_LDAP_PREFERENCE_ATTRIBUTE_PREFIX") or "").lower()

    for attribute, value in user_info.items():
        if isinstance(value, (list, tuple)):
            if not value:
                continue
            value = value[0]
        name = attribute.lower()
        if name == color:
            _add_entry(pref, "locationTeam", STRING, str(value))
        elif name == role:
            _add_entry(pref, "atakRoleType", STRING, str(value))
        elif name == callsign:
            _add_entry(pref, "locationCallsign", STRING, str(value))
        elif prefix and name.startswith(prefix):
            _add_entry(pref, attribute[len(prefix) :], STRING, str(value))


def _merge_settings(pref, settings):
    existing = {entry.get("key"): entry for entry in pref.iter("entry")}
    for setting in settings:
        key = setting["key"]
        if key in PROTECTED_KEYS:
            logger.warning(f"Ignoring reserved key {key} in device profile {setting.get('profile_name')}")
            continue
        value = setting["value"]
        value_class = setting["value_class"]

        entry = existing.get(key)
        if entry is None:
            existing[key] = _add_entry(pref, key, value_class, value)
            continue

        builtin_class = entry.get("class")
        if value_class != builtin_class:
            if builtin_class == BOOLEAN and str(value).strip().lower() in ("true", "false"):
                value = str(value).strip().lower()
            elif builtin_class != STRING:
                logger.warning(
                    f"Ignoring {key} from device profile {setting.get('profile_name')}: "
                    f"{value_class} doesn't fit the built-in {builtin_class}"
                )
                continue
        entry.text = value


def _builtin_preferences(enrollment: bool):
    root, pref = _new_preferences()
    get_ldap_attributes(pref)

    hostname = _hostname()
    port = app.config.get("RAVEN_MARTI_HTTPS_PORT")
    _add_entry(pref, "appMgmtEnableUpdateServer", BOOLEAN, "true")
    _add_entry(pref, "atakUpdateServerUrl", STRING, f"https://{hostname}:{port}/api/packages")
    _add_entry(pref, "repoStartupSync", BOOLEAN, "true")
    _add_entry(pref, "deviceProfileEnableOnConnect", BOOLEAN, "true")
    _add_entry(pref, "updateServerCaLocation", STRING, "/storage/emulated/0/atak/cert/truststore-root.p12")
    _add_entry(pref, "updateServerCaPassword", STRING, str(app.config.get("RAVEN_CA_PASSWORD")))
    _add_entry(pref, f"prefs_enable_channels_host-{hostname}", STRING, "true")
    _add_entry(pref, "prefs_enable_channels", STRING, "true" if app.config.get("RAVEN_ENABLE_CHANNELS") else "false")
    if not enrollment:
        _add_entry(pref, remote_clear.SERVER_PUBKEY_PREF_KEY, STRING, remote_clear.public_key_der_b64())
    return root, pref


# --- manifest ---


def _manifest(name: str, zip_entries) -> bytes:
    root = Element("MissionPackageManifest", {"version": "2"})
    configuration = SubElement(root, "Configuration")
    SubElement(configuration, "Parameter", {"name": "uid", "value": str(uuid.uuid4())})
    SubElement(configuration, "Parameter", {"name": "name", "value": name})
    SubElement(configuration, "Parameter", {"name": "onReceiveDelete", "value": "true"})
    contents = SubElement(root, "Contents")
    for zip_entry in zip_entries:
        SubElement(contents, "Content", {"ignore": "false", "zipEntry": zip_entry})
    return tostring(root)


# --- what goes in ---


def _recent_only(items, sync_secago: int):
    """Only the items of profiles changed within the last sync_secago seconds"""
    if sync_secago <= 0 or not items:
        return items
    cutoff = _utcnow() - datetime.timedelta(seconds=sync_secago)
    profile_ids = {item.get("profile_id") for item in items}
    updated = dict(
        db.session.execute(
            select(DeviceProfileSet.id, DeviceProfileSet.updated_at).filter(DeviceProfileSet.id.in_(profile_ids))
        ).all()
    )
    kept = []
    for item in items:
        updated_at = updated.get(item.get("profile_id"))
        if updated_at is not None and updated_at.tzinfo is not None:
            updated_at = updated_at.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        if updated_at is None or updated_at >= cutoff:
            kept.append(item)
    return kept


def _write_profile_files(archive, profile_files, skip=()):
    """Writes the profile files that exist and fit in the per-response cap. Returns their zip paths."""
    max_total = file_limits()["max_total_bytes"]
    total = 0
    written = []
    for profile_file in profile_files:
        path = profile_file.get("path")
        if not path or not os.path.isfile(path):
            logger.error(
                f"Device profile file {profile_file.get('zip_path')} of {profile_file.get('profile_name')} is missing"
            )
            continue
        size = profile_file.get("size")
        if size is None:
            size = os.path.getsize(path)
        if total + size > max_total:
            logger.error(
                f"Leaving {profile_file.get('zip_path')} of {profile_file.get('profile_name')} out of the profile: "
                "the files would exceed the size limit"
            )
            continue
        total += size
        archive.write(path, profile_file["zip_path"])
        written.append(profile_file["zip_path"])
    return written


def _selected_packages(enrollment: bool, sync_secago: int, user):
    plugin_query = select(Packages)
    package_query = select(DataPackage)
    if enrollment:
        plugin_query = plugin_query.filter(Packages.install_on_enrollment.is_(True))
        package_query = package_query.filter(DataPackage.install_on_enrollment.is_(True))
    else:
        plugin_query = plugin_query.filter(Packages.install_on_connection.is_(True))
        package_query = package_query.filter(DataPackage.install_on_connection.is_(True))
        if sync_secago > 0:
            cutoff = _utcnow() - datetime.timedelta(seconds=sync_secago)
            plugin_query = plugin_query.filter(Packages.publish_time >= cutoff)
            package_query = package_query.filter(DataPackage.submission_time >= cutoff)
    package_query = filter_by_groups(package_query, DataPackage, user)
    plugins = list(db.session.execute(plugin_query).scalars())
    packages = list(db.session.execute(package_query).scalars())
    return plugins, packages


def create_profile_zip(enrollment=True, syncSecago=-1, clientUid=None, username=None, authenticated=True):
    """Builds an enrollment or connection profile. Returns the zip as a seekable binary file,
    positioned at its end."""
    phase = "enrollment" if enrollment else "connection"
    user = app.security.datastore.find_user(username=username) if (authenticated and username) else None

    settings = resolve_settings(phase, username, clientUid)
    profile_files = resolve_files(phase, username, clientUid)
    if not enrollment:
        settings = _recent_only(settings, syncSecago)
        profile_files = _recent_only(profile_files, syncSecago)

    spool = _zip_spool()
    try:
        with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:
            maps = []
            if enrollment and app.config.get("RAVEN_PROFILE_MAP_SOURCES"):
                replaced = {profile_file["zip_path"].lower() for profile_file in profile_files}
                for map_file in sorted(os.listdir(_MAPS_FOLDER)):
                    zip_path = f"maps/{map_file}"
                    if zip_path.lower() in replaced or not os.path.isfile(os.path.join(_MAPS_FOLDER, map_file)):
                        continue
                    with open(os.path.join(_MAPS_FOLDER, map_file), "r") as f:
                        archive.writestr(zip_path, f.read())
                    maps.append(zip_path)

            files = _write_profile_files(archive, profile_files)

            plugins, packages = _selected_packages(enrollment, syncSecago, user)
            plugin_entries = []
            for plugin in plugins:
                path = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "packages", plugin.file_name)
                if not os.path.isfile(path):
                    logger.error(f"Plugin file {path} is missing, leaving it out of the profile")
                    continue
                zip_path = f"{SERVER_ZIP_FOLDER}/{plugin.file_name}"
                archive.write(path, zip_path)
                plugin_entries.append(zip_path)

            package_entries = []
            for package in packages:
                path = os.path.join(app.config.get("UPLOAD_FOLDER"), f"{package.hash}.zip")
                if not os.path.isfile(path):
                    logger.error(f"Data package file {path} is missing, leaving it out of the profile")
                    continue
                zip_path = f"{SERVER_ZIP_FOLDER}/{package.filename}"
                archive.write(path, zip_path)
                package_entries.append(zip_path)

            archive.writestr(
                _MANIFEST_PATH,
                _manifest(
                    "Device Profile",
                    [_PREF_PATH, _TRUSTSTORE_PATH] + maps + files + plugin_entries + package_entries,
                ),
            )

            root, pref = _builtin_preferences(enrollment)
            _merge_settings(pref, settings)
            archive.writestr(_PREF_PATH, tostring(root))

            archive.writestr(
                _TRUSTSTORE_PATH,
                export_onboard_truststore(
                    app.config.get("RAVEN_CA_FOLDER"),
                    app.config.get("RAVEN_CA_PASSWORD"),
                    app.config.get("RAVEN_CA_NAME"),
                ),
            )
    except BaseException:
        spool.close()
        raise
    return spool


def create_tool_profile_zip(settings, profile_files, tool):
    """Builds a tool profile: only the tool's settings and files. Returned positioned at 0."""
    spool = _zip_spool()
    try:
        with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:
            entries = []
            root, pref = _new_preferences()
            _merge_settings(pref, settings)
            if len(pref):
                archive.writestr(_PREF_PATH, tostring(root))
                entries.append(_PREF_PATH)
            entries += _write_profile_files(archive, profile_files)
            archive.writestr(_MANIFEST_PATH, _manifest(f"Device Profile ({tool})", entries))
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool


def _send_zip(spool):
    spool.seek(0)
    return send_file(spool, mimetype="application/zip", etag=False, conditional=False)


def connection_caller(client_uid):
    """(allowed, username, eud_uid, authenticated) for a connection or tool profile request."""
    username = None
    try:
        cert = verify_client_cert()
        if cert:
            username = cert.get_subject().commonName
    except BaseException as e:
        logger.debug(f"Unreadable client certificate on a profile request: {e}")

    if not username and request.headers.get("Authorization"):
        username = basic_auth(request.headers.get("Authorization"), consume_token=False) or None

    if not username:
        if app.config.get("RAVEN_CONNECTION_PROFILE_REQUIRE_AUTH"):
            return False, None, None, False
        return True, username_for_eud(client_uid), client_uid, False

    owner = username_for_eud(client_uid)
    if owner and owner.lower() != username.lower():
        logger.warning(f"{username} asked for the profile of {client_uid}, which belongs to {owner}")
        return True, username, None, True
    return True, username, client_uid, True


@device_profile_marti_api_blueprint.route("/Marti/api/tls/profile/enrollment", methods=["GET"])
def enrollment_profile():
    # A QR token signClient just used up is still good here, and isn't counted again
    username = basic_auth(request.headers.get("Authorization"), consume_token=False, check_token_max=False)
    if not username:
        return "", 401

    client_uid = request.args.get("clientUid") or request.args.get("clientUID")
    owner = username_for_eud(client_uid)
    if owner and owner.lower() != username.lower():
        client_uid = None

    try:
        return _send_zip(create_profile_zip(True, -1, client_uid, username))
    except BaseException as e:
        logger.error(f"Failed to build the enrollment profile for {username}: {e}")
        return "", 500


@device_profile_marti_api_blueprint.route("/api/connection", methods=["GET"])
@device_profile_marti_api_blueprint.route("/Marti/api/device/profile/connection", methods=["GET"])
def connection_profile():
    allowed, username, client_uid, authenticated = connection_caller(_request_uid())
    if not allowed:
        return "", 401

    try:
        return _send_zip(create_profile_zip(False, _sync_secago(), client_uid, username, authenticated))
    except BaseException as e:
        logger.error(f"Failed to build the connection profile for {username}: {e}")
        return "", 500


@device_profile_marti_api_blueprint.route("/Marti/api/device/profile/tool/<tool>", methods=["GET"])
def tool_profile(tool):
    allowed, username, client_uid, _authenticated = connection_caller(_request_uid())
    if not allowed:
        return "", 401

    try:
        tool = validate_tool(tool)
    except ValueError:
        tool = None
    if not tool:
        return "", 204

    try:
        sync_secago = _sync_secago()
        settings = _recent_only(resolve_settings("tool", username, client_uid, tool), sync_secago)
        profile_files = _recent_only(resolve_files("tool", username, client_uid, tool), sync_secago)
        if not settings and not profile_files:
            return "", 204
        return _send_zip(create_tool_profile_zip(settings, profile_files, tool))
    except BaseException as e:
        logger.error(f"Failed to build the {tool} tool profile for {username}: {e}")
        return "", 500

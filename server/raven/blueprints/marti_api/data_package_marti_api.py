"""TAK data package (file share) API: upload, the tool flag, search/list and download, plus the
access rules (group isolation, private user-to-user packages) that the web and mission APIs reuse.

verify_client_cert is looked up through this module at call time so tests can replace it.
"""

import datetime
import hashlib
import io
import os
import uuid
import zipfile
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, SubElement, tostring

import bleach
import sqlalchemy.exc
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request, send_file
from flask_babel import gettext
from sqlalchemy import func, or_, select
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from raven import metrics
from raven.audit import record_audit_event
from raven.blueprints.marti_api.marti_api import verify_client_cert
from raven.extensions import db, logger
from raven.functions import format_bytes, iso8601_string_from_datetime
from raven.group_isolation import assign_uploader_groups, group_visibility_clause, is_admin
from raven.mission_export import content_file_path
from raven.models.DataPackage import DataPackage
from raven.models.DataPackageRecipient import DataPackageRecipient
from raven.models.EUD import EUD
from raven.models.GroupUser import GroupUser
from raven.models.MissionContent import MissionContent
from raven.models.MissionRole import MissionRole

data_package_marti_api = Blueprint("data_package_marti_api", __name__)

_RESOURCE_TYPE = "gov.tak.api.comms.takserver.mission.data.Resource"


class DataPackageUploadError(Exception):
    """An upload that must be refused, with the status to answer"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status

    def response(self):
        return jsonify({"success": False, "error": self.message}), self.status


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


def _forbidden():
    return jsonify({"success": False, "error": "Forbidden"}), 403


def _hostname():
    return urlparse(request.url_root).hostname


def _tool_url(file_hash):
    return f"https://{_hostname()}:{app.config.get('RAVEN_MARTI_HTTPS_PORT')}/Marti/api/sync/metadata/{file_hash}/tool"


# --- who is asking, and what they may see ---


def data_package_caller():
    """The web session's user, else the active user named by the client cert, else None. Never raises."""
    from flask_login import current_user

    try:
        if current_user and current_user.is_authenticated:
            return current_user if getattr(current_user, "active", True) else None
    except BaseException:
        pass

    try:
        cert = verify_client_cert()
        if not cert:
            return None
        username = cert.get_subject().commonName
        user = app.security.datastore.find_user(username=username) if username else None
    except BaseException as e:
        logger.debug(f"No data package caller from the client certificate: {e}")
        return None
    if user is None or not user.active:
        return None
    return user


def _user_is_admin(user) -> bool:
    try:
        return is_admin(user)
    except BaseException:
        return False


def visible_data_packages(query, user, admin_sees_all=False):
    """Restricts a DataPackage query or select to the packages user may see."""
    admin = _user_is_admin(user)
    if admin_sees_all and admin:
        return query

    public = or_(DataPackage.tool.is_(None), func.lower(DataPackage.tool) != "private")
    if not admin:
        clause = group_visibility_clause(DataPackage, user)
        if clause is not None:
            public = public & clause

    if user is None or getattr(user, "id", None) is None:
        return query.filter(public)

    eud_uids = select(EUD.uid).where(EUD.user_id == user.id)
    callsigns = select(EUD.callsign).where(EUD.user_id == user.id, EUD.callsign.is_not(None))
    own = or_(
        DataPackage.submission_user == user.id,
        DataPackage.creator_uid.in_(eud_uids),
        DataPackage.recipients.any(
            or_(DataPackageRecipient.eud_uid.in_(eud_uids), DataPackageRecipient.callsign.in_(callsigns))
        ),
    )
    return query.filter(or_(public, own))


def can_access_data_package(dp, user) -> bool:
    if dp is None:
        return False
    query = visible_data_packages(select(DataPackage.id).where(DataPackage.hash == dp.hash), user)
    return db.session.execute(query).first() is not None


# --- storing uploads ---


def _upload_folder():
    return app.config.get("UPLOAD_FOLDER")


def _check_hash_param(file_hash):
    claimed = request.args.get("hash")
    if claimed and claimed.strip().lower() != file_hash:
        raise DataPackageUploadError(gettext("The hash parameter does not match the uploaded file"), 400)


def _check_existing(file_hash, username):
    """True when the package is already stored and the uploader may see it (idempotent upload)."""
    existing = db.session.execute(select(DataPackage).filter_by(hash=file_hash)).scalar()
    if existing is None:
        return False
    uploader = app.security.datastore.find_user(username=username) if username else data_package_caller()
    if can_access_data_package(existing, uploader):
        return True
    raise DataPackageUploadError(gettext("This data package has already been uploaded"), 409)


def _write_package_file(data: bytes, file_hash: str):
    """Stores the bytes at <UPLOAD_FOLDER>/<hash>.zip unless that file already exists."""
    folder = _upload_folder()
    path = os.path.join(folder, f"{file_hash}.zip")
    if os.path.exists(path):
        return
    temp = os.path.join(folder, f".upload-{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "wb") as f:
            f.write(data)
        if os.path.exists(path):
            os.remove(temp)
        else:
            os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.remove(temp)
        raise


def save_data_package_to_db(
    filename=None, sha256_hash=None, mimetype="application/zip", file_size=0, username=None, eud_uid=None
):
    creator_uid = eud_uid or request.args.get("creatorUid") or request.args.get("CreatorUid")
    creator = db.session.execute(select(EUD).filter_by(uid=creator_uid)).scalar() if creator_uid else None

    uploader = None
    if username:
        uploader = app.security.datastore.find_user(username=username)
    if uploader is None:
        uploader = data_package_caller()
    if uploader is None and creator is not None:
        uploader = creator.user

    dp = DataPackage()
    dp.filename = filename
    dp.hash = sha256_hash
    dp.creator_uid = creator.uid if creator else None
    dp.submission_time = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    dp.mime_type = mimetype
    dp.size = file_size
    dp.submission_user = uploader.id if uploader is not None else None
    assign_uploader_groups(dp, uploader)

    try:
        db.session.add(dp)
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
        raise DataPackageUploadError(gettext("This data package has already been uploaded"), 409)


def save_data_package_file(file, filename=None, username=None, eud_uid=None) -> str:
    """Stores an uploaded zip (bytes or a FileStorage) and its row. Returns the sha256 hash."""
    if isinstance(file, (bytes, bytearray)):
        data = bytes(file)
        name = filename
    else:
        data = file.read()
        file.stream.seek(0)
        name = filename or file.filename

    file_hash = hashlib.sha256(data).hexdigest()
    _check_hash_param(file_hash)
    if _check_existing(file_hash, username):
        return file_hash

    stem = os.path.splitext(secure_filename(name or ""))[0] or uuid.uuid4().hex
    _write_package_file(data, file_hash)
    save_data_package_to_db(
        f"{stem}.zip", file_hash, "application/x-zip-compressed", len(data), username=username, eud_uid=eud_uid
    )
    return file_hash


def _manifest_parameter(parent, name, value):
    SubElement(parent, "Parameter", {"name": name, "value": value})


def create_data_package_zip(file) -> str:
    """Wraps a single non-zip file (a FileStorage or a path) in a new data package. Returns its hash."""
    if isinstance(file, FileStorage):
        data = file.read()
        file.stream.seek(0)
        original_name = file.filename or ""
    else:
        with open(file, "rb") as f:
            data = f.read()
        original_name = os.path.basename(file)

    name, extension = os.path.splitext(original_name)
    entry_name = secure_filename(name + extension) or uuid.uuid4().hex
    folder = hashlib.md5(data).hexdigest()
    zip_entry = f"{folder}/{entry_name}"

    manifest = Element("MissionPackageManifest", {"version": "2"})
    configuration = SubElement(manifest, "Configuration")
    _manifest_parameter(configuration, "uid", str(uuid.uuid4()))
    _manifest_parameter(configuration, "name", entry_name)
    contents = SubElement(manifest, "Contents")
    content = SubElement(contents, "Content", {"ignore": "false", "zipEntry": zip_entry})
    if extension.lower() in (".kml", ".kmz"):
        _manifest_parameter(content, "name", entry_name)
        _manifest_parameter(content, "contentType", "KML")
        _manifest_parameter(content, "visible", "true")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zip_entry, data)
        archive.writestr("MANIFEST/manifest.xml", tostring(manifest))
    zip_bytes = buffer.getvalue()

    file_hash = hashlib.sha256(zip_bytes).hexdigest()
    if _check_existing(file_hash, None):
        return file_hash

    _write_package_file(zip_bytes, file_hash)
    package_name = secure_filename(name) or uuid.uuid4().hex
    save_data_package_to_db(f"{package_name}.zip", file_hash, "application/zip", len(zip_bytes))
    return file_hash


def _allowed_extensions() -> set[str]:
    allowed = app.config.get("ALLOWED_EXTENSIONS") or []
    if isinstance(allowed, str):
        allowed = allowed.split(",")
    return {str(extension).strip().lower() for extension in allowed}


# --- routes ---


@data_package_marti_api.route("/Marti/sync/missionupload", methods=["POST"])
def data_package_share():
    uploaded = next(iter(request.files.values()), None)
    if uploaded is None:
        return _error(gettext("A file is required"), 400)

    filename = uploaded.filename or ""
    extension = filename.rsplit(".", 1)[1] if "." in filename else ""
    if not extension and "zip" in (uploaded.mimetype or ""):
        extension = "zip"
    if extension.lower() not in _allowed_extensions():
        return _error(gettext("Invalid file extension: %(extension)s", extension=extension), 400)

    try:
        if extension == "zip":
            file_hash = save_data_package_file(uploaded)
        else:
            file_hash = create_data_package_zip(uploaded)
    except DataPackageUploadError as e:
        return e.response()

    metrics.record("raven_data_package_uploads_total")
    return _tool_url(file_hash), 200


def _package_by_hash(file_hash):
    return db.session.execute(select(DataPackage).filter_by(hash=file_hash)).scalar()


@data_package_marti_api.route("/Marti/api/sync/metadata/<file_hash>/tool", methods=["GET", "PUT"])
def data_package_metadata(file_hash):
    dp = _package_by_hash(file_hash)

    if request.method == "PUT":
        try:
            if dp is None or not can_access_data_package(dp, data_package_caller()):
                return "", 404
            tool = bleach.clean(request.get_data().decode("utf-8")).strip()
            if tool:
                dp.tool = tool
                db.session.commit()
            return "", 200
        except BaseException as e:
            db.session.rollback()
            logger.error(f"Failed to set the tool of data package {file_hash}: {e}")
            return jsonify({"error": str(e)}), 500

    if dp is None:
        return "", 404
    if not can_access_data_package(dp, data_package_caller()):
        return _forbidden()
    path = os.path.join(_upload_folder(), f"{dp.hash}.zip")
    if not os.path.isfile(path):
        return "", 404
    return send_file(path, download_name=dp.filename)


def _submitter(dp):
    return dp.user.username if dp.user else "anonymous"


@data_package_marti_api.route("/Marti/api/sync/search", methods=["GET"])
def data_package_search():
    query = select(DataPackage)
    for parameter in ("hash", "uid"):
        value = request.args.get(parameter)
        if value:
            query = query.filter(DataPackage.hash == value)
    query = visible_data_packages(query, data_package_caller())

    data = []
    for dp in db.session.execute(query).scalars():
        try:
            expiration = int(dp.expiration) if dp.expiration is not None else None
        except (TypeError, ValueError):
            logger.error(f"Data package {dp.hash} has an invalid expiration {dp.expiration!r}, skipping it")
            continue
        data.append(
            {
                "filename": dp.filename,
                "keywords": dp.keywords.split(",") if dp.keywords else [],
                "mimeType": dp.mime_type,
                "name": dp.filename,
                "submissionTime": iso8601_string_from_datetime(dp.submission_time),
                "submitter": _submitter(dp),
                "uid": dp.hash,
                "creatorUid": dp.creator_uid,
                "hash": dp.hash,
                "size": int(dp.size) if dp.size is not None else 0,
                "tool": dp.tool or "public",
                "groups": dp.group_names,
                "expiration": expiration,
                "latitude": 0,
                "longitude": 0,
                "altitude": 0,
            }
        )

    return jsonify(
        {
            "version": "3",
            "type": _RESOURCE_TYPE,
            "data": data,
            "messages": [],
            "nodeId": app.config.get("RAVEN_NODE_ID"),
        }
    )


@data_package_marti_api.route("/Marti/sync/search", methods=["GET"])
def tak_search():
    query = select(DataPackage)
    uid = request.args.get("uid")
    if uid:
        query = query.filter(DataPackage.hash == uid)
    query = visible_data_packages(query, data_package_caller())

    results = []
    for dp in db.session.execute(query).scalars():
        results.append(
            {
                "UID": dp.hash,
                "Name": dp.filename,
                "Hash": dp.hash,
                "CreatorUid": dp.creator_uid,
                "SubmissionDateTime": (
                    dp.submission_time.strftime("%Y-%m-%dT%H:%M:%S.000Z") if dp.submission_time else None
                ),
                "EXPIRATION": "-1",
                "Keywords": ["missionpackage"],
                "MIMEType": dp.mime_type,
                "Size": str(dp.size),
                "SubmissionUser": _submitter(dp),
                "PrimaryKey": str(dp.id),
                "Tool": dp.tool or "public",
            }
        )

    return jsonify({"resultCount": len(results), "results": results})


def _file_not_found(filename):
    return _error(gettext("File not found: %(filename)s}", filename=filename), 404)


def _can_see_mission_content(content, user) -> bool:
    """Whether the caller may download mission content: the same rules as listing and subscribing.

    Allowed when the content is in no mission, or one of its missions is ungrouped or in __ANON__,
    or the caller is an administrator, holds a role in (is subscribed to) one of its missions, or
    has an active membership in either direction of one of its missions' groups."""
    missions = list(content.mission or [])
    if not missions or _user_is_admin(user):
        return True

    user_id = getattr(user, "id", None) if user is not None else None
    eud_uids = select(EUD.uid).where(EUD.user_id == user_id)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    for mission in missions:
        groups = list(mission.groups or [])
        if not groups or any(group.name == "__ANON__" for group in groups):
            return True
        if user_id is None:
            continue

        role = db.session.execute(
            select(MissionRole.id).where(
                MissionRole.mission_name == mission.name,
                or_(
                    func.lower(MissionRole.username) == (user.username or "").lower(),
                    MissionRole.clientUid.in_(eud_uids),
                ),
            )
        ).first()
        if role is not None:
            return True

        membership = db.session.execute(
            select(GroupUser.group_id).where(
                GroupUser.user_id == user_id,
                GroupUser.group_id.in_([group.id for group in groups]),
                or_(GroupUser.enabled.is_(None), GroupUser.enabled.is_(True)),
                or_(GroupUser.expires_at.is_(None), GroupUser.expires_at > now),
            )
        ).first()
        if membership is not None:
            return True

    return False


@data_package_marti_api.route("/Marti/sync/content", methods=["GET", "HEAD"])
def download_data_package():
    file_hash = request.args.get("hash")
    head = request.method == "HEAD"

    dp = _package_by_hash(file_hash) if file_hash else None
    caller = data_package_caller()
    if dp is not None and not can_access_data_package(dp, caller):
        return ("", 403) if head else _forbidden()

    content = None
    if dp is None and file_hash:
        content = db.session.execute(select(MissionContent).filter_by(hash=file_hash)).scalar()
        if content is not None and not _can_see_mission_content(content, caller):
            return ("", 403) if head else _forbidden()

    if dp is None and content is None:
        if head:
            return "", 404
        return _error(f"No data package found with hash {file_hash}", 404)

    if head:
        return "", 200

    if content is not None:
        path = content_file_path(content)
        if not path:
            return _file_not_found(content.filename)
        metrics.record("raven_data_package_downloads_total")
        return send_file(path, download_name=content.filename)

    certificate = dp.certificate
    if certificate is not None:
        record_audit_event(
            "enrollment",
            "enroll_zip",
            username=certificate.username,
            actor=caller.username if caller is not None else None,
            details=f"Certificate package {dp.filename} downloaded over Marti sync",
        )

    _, extension = os.path.splitext(dp.filename or "")
    candidates = [
        os.path.join(_upload_folder(), f"{dp.hash}{extension}"),
        os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "missions", dp.filename or ""),
    ]
    for path in candidates:
        if dp.filename and os.path.isfile(path):
            metrics.record("raven_data_package_downloads_total")
            return send_file(path, download_name=dp.filename)
    return _file_not_found(dp.filename)


@data_package_marti_api.route("/Marti/sync/missionquery", methods=["GET"])
def data_package_query():
    file_hash = request.args.get("hash")
    packages = list(db.session.execute(select(DataPackage).filter_by(hash=file_hash)).scalars())
    if len(packages) == 1 and can_access_data_package(packages[0], data_package_caller()):
        return _tool_url(file_hash), 200
    return jsonify({"success": False, "error": "File not found"}), 404


def _file_metadata(package) -> dict:
    """One entry of /Marti/api/files/metadata (TAK's field names)."""
    owner = package.user
    sender = package.eud
    return {
        "Hash": package.hash,
        "Name": package.filename,
        "MimeType": package.mime_type,
        "Size": format_bytes(package.size),
        "Keywords": package.keywords,
        "Expiration": package.expiration,
        "Time": iso8601_string_from_datetime(package.submission_time),
        "User": owner.username if owner else None,
        "Creator": sender.callsign if sender else None,
    }


@data_package_marti_api.route("/Marti/api/files/metadata", methods=["GET"])
def files_metadata():
    query = select(DataPackage)
    name = request.args.get("name")
    if name:
        query = query.filter(DataPackage.filename == bleach.clean(name))
    query = visible_data_packages(query, data_package_caller())

    data = [_file_metadata(package) for package in db.session.execute(query).scalars()]

    return jsonify({"version": "3", "type": _RESOURCE_TYPE, "data": data, "nodeId": app.config.get("RAVEN_NODE_ID")})

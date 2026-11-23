"""
Local user administration for the web UI's Users page.

Create, delete, activate and deactivate users; roles and read-only admin areas; passwords (admin
reset, forced change, temporary password); website access; device (EUD) assignment; group
memberships; user filters (saved user lists); sending a file to a user's devices; Remote Clear of
a user's devices; per-user certificate validity.

Strings from the request are bleach-cleaned before use, passwords included: stored hashes were made
from the cleaned form, so it has to stay that way or existing passwords stop verifying.
"""

import datetime
import json
import os
import secrets
import string
import traceback
import uuid
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, SubElement, tostring

import bleach
import pika
import sqlalchemy
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from flask_security import admin_change_password, auth_required, current_user, hash_password, roles_accepted
from werkzeug.utils import secure_filename

from raven import cert_revocation
from raven.admin_view import ADMIN_AREAS, VIEWER_ROLE, admin_view, parse_areas
from raven.audit import record_audit_event
from raven.blueprints.raven_api.api import paginate, search
from raven.cert_validity import max_cert_days, resolve_cert_days, validate_cert_days
from raven.extensions import db, logger
from raven.functions import add_unknown_point, iso8601_string_from_datetime
from raven.models.Certificate import Certificate
from raven.models.DataPackage import DataPackage
from raven.models.EUD import EUD
from raven.models.Group import Group
from raven.models.GroupUser import GroupUser
from raven.models.UserFilter import UserFilter
from raven.models.UserFilterMember import UserFilterMember

user_api_blueprint = Blueprint("user_api_blueprint", __name__)

ADMIN_ROLE = "administrator"
# No characters that are easy to misread
_TEMP_PASSWORD_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0OoIl1")
_TEMP_PASSWORD_LENGTH = 12
_TRUTHY = ("1", "true", "yes")


# -------------------------------------------------------------------------------------- helpers


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def _ok(**extra):
    return jsonify({"success": True, **extra})


def _ldap_guard(message):
    """The error response when LDAP manages users, else None."""
    if app.config.get("RAVEN_ENABLE_LDAP"):
        return _error(message, 400)
    return None


def _clean(value):
    return bleach.clean(value)


def _json():
    return request.get_json(silent=True) or {}


def _find_user(username):
    if not username:
        return None
    return app.security.datastore.find_user(username=username)


def _is_admin():
    return current_user.has_role(ADMIN_ROLE)


def _role_names(user):
    return [role.name for role in user.roles]


def _is_caller(username) -> bool:
    """Exactly the caller's username."""
    return username == current_user.username


def _resolves_to_caller(username) -> bool:
    """The caller's username, or a name the datastore lookup resolves to the caller's own account."""
    if _is_caller(username):
        return True
    user = _find_user(username) if isinstance(username, str) else None
    return user is not None and user.id == current_user.id


def _protected_user_response(username: str):
    """A 403 response when username is a protected system account that isn't the caller's own,
    else None. Exact comparisons, like the datastore lookups (find_user is case-sensitive)."""
    if _is_caller(username):
        return None
    protected = app.config.get("RAVEN_PROTECTED_USERNAMES") or []
    if username not in protected:
        return None
    return _error(gettext("%(username)s is a protected system account and can't be modified", username=username), 403)


def _resolve_roles(names):
    """(roles, error response). Cleans each name, drops duplicates, keeps order."""
    roles = []
    for name in names:
        name = _clean(name)
        role = app.security.datastore.find_role(name)
        if role is None:
            return None, _error(gettext("Role %(role)s does not exist", role=name), 400)
        if role not in roles:
            roles.append(role)
    return roles, None


def _rabbitmq_connection():
    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=app.config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"),
            credentials=pika.PlainCredentials(
                app.config.get("RAVEN_RABBITMQ_USERNAME"), app.config.get("RAVEN_RABBITMQ_PASSWORD")
            ),
        )
    )


def _audit(category, action, username, **kwargs):
    record_audit_event(category, action, username=username, actor=current_user.username, **kwargs)


# --------------------------------------------------------------------------- create / delete


@user_api_blueprint.route("/api/user/add", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def create_user():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to create users"))
    if refused:
        return refused

    from raven.UsernameValidator import UsernameValidator

    body = _json()
    username = _clean(body.get("username"))
    validation = UsernameValidator(app).validate(username)
    if validation and validation[0]:
        return _error(validation[0], 400)

    password = _clean(body.get("password"))
    confirm_password = _clean(body.get("confirm_password"))
    if password != confirm_password:
        return _error(gettext("Passwords do not match"), 400)

    roles, error = _resolve_roles(body.get("roles") or ["user"])
    if error:
        return error
    if any(role.name == ADMIN_ROLE for role in roles) and not _is_admin():
        return _error(gettext("Only administrators can add users to the administrators role"), 403)

    datastore = app.security.datastore
    if datastore.find_user(username=username):
        return _error(gettext("User %(username)s already exists", username=username), 400)

    user = datastore.create_user(
        username=username, password=hash_password(password), roles=roles, force_password_change=True
    )
    db.session.commit()

    anon = db.session.query(Group).filter_by(name="__ANON__").first()
    if anon is not None:
        for direction in ("IN", "OUT"):
            membership = GroupUser()
            membership.user_id = user.id
            membership.group_id = anon.id
            membership.direction = direction
            membership.enabled = True
            try:
                db.session.add(membership)
                db.session.commit()
            except sqlalchemy.exc.IntegrityError:
                db.session.rollback()

    _audit("user_admin", "user_created", user.username, details="Roles: " + ", ".join(r.name for r in roles))

    from raven.group_policy import apply_auto_join

    apply_auto_join(user, actor=current_user.username)
    return _ok()


@user_api_blueprint.route("/api/user/delete", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def delete_user():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to delete users"))
    if refused:
        return refused

    username = _clean(_json().get("username"))
    if _resolves_to_caller(username):
        return _error(gettext("You can't delete your own account"), 400)
    protected = _protected_user_response(username)
    if protected:
        return protected

    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 400)

    try:
        # Looked up now so it can be replaced (tests)
        from raven.blueprints.raven_api import certificate_api

        try:
            certificate_api.revoke_user_certificates(
                user.username, "cessationOfOperation", current_user.username, note="User deleted"
            )
        except BaseException as e:
            db.session.rollback()
            logger.error(f"Failed to revoke {user.username}'s certificates: {e}")
            logger.debug(traceback.format_exc())

        db.session.query(GroupUser).filter(GroupUser.user_id == user.id).delete(synchronize_session=False)
        db.session.query(UserFilterMember).filter(UserFilterMember.user_id == user.id).delete(synchronize_session=False)
        app.security.datastore.delete_user(user)
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to delete user {username}: {e}")
        logger.debug(traceback.format_exc())
        return _error(gettext("Failed to delete user: %(e)s", e=str(e)), 400)

    _audit("user_admin", "user_deleted", username)
    return _ok()


# ----------------------------------------------------------------------------------- passwords


@user_api_blueprint.route("/api/user/password/reset", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def admin_reset_password():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to reset passwords"))
    if refused:
        return refused

    body = _json()
    username = _clean(body.get("username") or "")
    new_password = _clean(body.get("new_password") or "")
    if not username or not new_password:
        return _error(gettext("Please specify a username and new password"), 400)

    minimum = app.config.get("SECURITY_PASSWORD_LENGTH_MIN", 9)
    if len(new_password) < minimum:
        return _error(gettext("Your password must be at least %(characters)s characters long", characters=minimum))
    if ":" in new_password or "@" in new_password:
        return _error(gettext("Passwords should not include @ or : characters"), 400)

    protected = _protected_user_response(username)
    if protected:
        return protected
    user = _find_user(username)
    if not user:
        return _error(gettext("Could not find user %(username)s", username=username), 400)

    admin_change_password(user, new_password, notify=False)
    # The password_changed signal handler cleared it: the user still has to pick their own
    user.force_password_change = True
    db.session.commit()
    return _ok()


@user_api_blueprint.route("/api/user/force_password_reset", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def force_password_reset():
    username = _clean(_json().get("username") or "")
    if not username:
        return _error(gettext("Please specify a username"), 400)
    protected = _protected_user_response(username)
    if protected:
        return protected
    user = _find_user(username)
    if not user:
        return _error(gettext("Could not find user %(username)s", username=username), 400)

    user.force_password_change = True
    db.session.commit()
    _audit(
        "user_admin",
        "force_password_change",
        user.username,
        details="User must set a new password at next login",
    )
    return _ok()


@user_api_blueprint.route("/api/user/issue_temp_password", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def issue_temp_password():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to reset passwords"))
    if refused:
        return refused

    username = _clean(_json().get("username") or "")
    if not username:
        return _error(gettext("Please specify a username"), 400)
    protected = _protected_user_response(username)
    if protected:
        return protected
    user = _find_user(username)
    if not user:
        return _error(gettext("Could not find user %(username)s", username=username), 400)

    password = "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(_TEMP_PASSWORD_LENGTH))
    admin_change_password(user, password, notify=False)
    user.force_password_change = True
    db.session.commit()
    return _ok(password=password)


# ------------------------------------------------------------------- activation and web access


def _kick_euds(user):
    """Tells each streaming connection of the user's EUDs to drop. Best effort."""
    uids = [eud.uid for eud in user.euds]
    if not uids:
        return
    connection = None
    try:
        connection = _rabbitmq_connection()
        channel = connection.channel()
        for uid in uids:
            channel.basic_publish(exchange="dms", routing_key=uid, body=json.dumps({"uid": uid, "kick": True}))
    except BaseException as e:
        logger.error(f"Failed to disconnect {user.username}'s devices: {e}")
        logger.debug(traceback.format_exc())
    finally:
        if connection is not None:
            try:
                connection.close()
            except BaseException:
                pass


def _kick_cert_sessions(user):
    """Drops live SSL streaming sessions on the user's unrevoked certificates. Best effort."""
    try:
        name = user.username.lower()
        serials = (
            db.session.execute(
                sqlalchemy.select(Certificate.serial_number).where(
                    Certificate.revoked_at.is_(None),
                    Certificate.serial_number.isnot(None),
                    sqlalchemy.or_(
                        sqlalchemy.func.lower(Certificate.username) == name,
                        sqlalchemy.and_(
                            Certificate.username.is_(None), sqlalchemy.func.lower(Certificate.common_name) == name
                        ),
                    ),
                )
            )
            .scalars()
            .all()
        )
        if serials:
            cert_revocation.kick_revoked_sessions(app._get_current_object(), serials, logger)
    except BaseException as e:
        logger.error(f"Failed to disconnect {user.username}'s certificate sessions: {e}")
        logger.debug(traceback.format_exc())


@user_api_blueprint.route("/api/user/deactivate", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def deactivate_user():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to deactivate users"))
    if refused:
        return refused

    username = _clean(_json().get("username", ""))
    if not username:
        return _error(gettext("Please specify the username to deactivate"), 400)
    if _resolves_to_caller(username):
        return _error(gettext("You can't deactivate your own account"), 400)
    protected = _protected_user_response(username)
    if protected:
        return protected
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 400)
    if not user.active:
        return jsonify({"success": False, "error": gettext("%(username)s is already deactivated", username=username)})

    app.security.datastore.deactivate_user(user)
    db.session.commit()
    _audit("user_admin", "user_disabled", user.username)

    _kick_euds(user)
    # Certificates stay valid: reactivating the account restores access
    _kick_cert_sessions(user)
    return _ok()


@user_api_blueprint.route("/api/user/activate", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def activate_user():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to activate users"))
    if refused:
        return refused

    username = _clean(_json().get("username") or "")
    if not username:
        return _error(gettext("Please specify the username to activate"), 400)
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 400)
    if user.active:
        return jsonify({"success": False, "error": gettext("%(username)s is already activated", username=username)})

    app.security.datastore.activate_user(user)
    db.session.commit()
    _audit("user_admin", "user_enabled", user.username)
    return _ok()


@user_api_blueprint.route("/api/user/site_access/grant", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def grant_site_access():
    username = _clean(_json().get("username") or "")
    if not username:
        return _error(gettext("Please specify the username to grant access to"), 400)
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 400)

    user.site_access = True
    db.session.commit()
    _audit("user_admin", "site_access_granted", user.username)
    return _ok()


@user_api_blueprint.route("/api/user/site_access/revoke", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def revoke_site_access():
    username = _clean(_json().get("username") or "")
    if not username:
        return _error(gettext("Please specify the username to revoke access from"), 400)
    if _resolves_to_caller(username):
        return _error(gettext("You can't revoke your own website access"), 400)
    protected = _protected_user_response(username)
    if protected:
        return protected
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 400)

    user.site_access = False
    db.session.commit()
    _audit("user_admin", "site_access_revoked", user.username)
    return _ok()


# ------------------------------------------------------------------------- roles and areas


@user_api_blueprint.route("/api/user/role", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def set_user_role():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to assign roles"))
    if refused:
        return refused

    body = _json()
    username = body.get("username")
    names = body.get("roles")
    if not username or not names:
        return _error(gettext("Please specify a username and roles"), 400)
    username = _clean(username)
    if _resolves_to_caller(username):
        return _error(gettext("You can't change your own role"), 400)
    protected = _protected_user_response(username)
    if protected:
        return protected

    roles, error = _resolve_roles(names)
    if error:
        return error
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 400)

    old = _role_names(user)
    datastore = app.security.datastore
    for role in list(user.roles):
        datastore.remove_role_from_user(user, role)
    for role in roles:
        datastore.add_role_to_user(user, role)

    new = [role.name for role in roles]
    if VIEWER_ROLE not in new or ADMIN_ROLE in new:
        user.admin_view_areas = None
    db.session.commit()

    _audit(
        "user_admin",
        "role_changed",
        user.username,
        details="Roles {} -> {}".format(", ".join(old) or "none", ", ".join(new)),
    )
    return _ok()


@user_api_blueprint.route("/api/user/admin_view", methods=["PUT"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def set_admin_view_areas():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    username = body.get("username")
    areas = body.get("areas")
    if not username or not isinstance(username, str) or not isinstance(areas, list):
        return _error(gettext("Please specify a username and areas"), 400)

    for area in areas:
        if area not in ADMIN_AREAS:
            return _error(gettext("Unknown admin area: %(area)s", area=_clean(str(area))), 400)

    username = _clean(username)
    protected = _protected_user_response(username)
    if protected:
        return protected
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 400)
    if not user.has_role(VIEWER_ROLE) or user.has_role(ADMIN_ROLE):
        return _error(gettext("%(username)s is not a read-only admin", username=username), 400)

    old = parse_areas(user.admin_view_areas)
    new = [area for area in ADMIN_AREAS if area in areas]
    user.admin_view_areas = json.dumps(new)
    db.session.commit()

    _audit(
        "user_admin",
        "admin_view_changed",
        user.username,
        details="Admin areas {} -> {}".format(", ".join(old) or "none", ", ".join(new) or "none"),
    )
    return _ok(areas=new)


# ------------------------------------------------------------------------------- devices


@user_api_blueprint.route("/api/user/assign_eud", methods=["POST"])
@auth_required()
def assign_eud_to_user():
    body = _json()
    uid = body.get("uid")
    if uid:
        uid = _clean(uid)
    if not uid:
        return _error("Please specify an EUD", 400)

    username = body.get("username")
    if username:
        username = _clean(username)
    is_admin = _is_admin()

    if not username or _resolves_to_caller(username):
        user = current_user
    elif not is_admin:
        return _error(gettext("Only administrators can assign devices to others"), 403)
    else:
        user = _find_user(username)
        if not user:
            return _error(gettext("User %(username)s does not exist", username=username), 404)

    eud = db.session.query(EUD).filter(EUD.uid == uid).first()
    if not eud:
        return _error(gettext("EUD %(eud_uid)s not found", eud_uid=uid), 404)

    if not is_admin:
        if eud.user_id is not None and eud.user_id != user.id:
            return _error(gettext("%(uid)s is already assigned to another user", uid=uid), 403)
        if eud.user_id is None:
            issued = (
                db.session.query(Certificate)
                .filter(
                    Certificate.eud_uid == uid,
                    Certificate.revoked_at.is_(None),
                    sqlalchemy.func.lower(Certificate.username) == user.username.lower(),
                )
                .first()
            )
            if issued is None:
                return _error(gettext("Ask an administrator to assign %(uid)s to you", uid=uid), 403)

    eud.user_id = user.id
    db.session.commit()
    return _ok()


def _fileshare_cot(package, eud_callsign, sender_url):
    now = datetime.datetime.now(datetime.timezone.utc)
    event = Element(
        "event",
        {
            "type": "b-f-t-r",
            "how": "h-e",
            "version": "2.0",
            "uid": str(uuid.uuid4()),
            "start": iso8601_string_from_datetime(now),
            "time": iso8601_string_from_datetime(now),
            "stale": iso8601_string_from_datetime(now + datetime.timedelta(hours=1)),
        },
    )
    add_unknown_point(event)
    detail = SubElement(event, "detail")
    SubElement(
        detail,
        "fileshare",
        {
            "filename": package.filename,
            "senderUrl": sender_url,
            "sizeInBytes": str(package.size),
            "sha256": package.hash,
            "senderUid": app.config.get("RAVEN_SERVER_SENDER_UID", "server-uid"),
            "senderCallsign": app.config.get("RAVEN_SERVER_SENDER_CALLSIGN", "Server"),
            "name": package.filename,
        },
    )
    SubElement(detail, "ackrequest", {"uid": str(uuid.uuid4()), "ackrequested": "true", "tag": package.filename})
    marti = SubElement(detail, "marti")
    SubElement(marti, "dest", {"callsign": eud_callsign or ""})
    return tostring(event).decode("utf-8")


@user_api_blueprint.route("/api/user/send_file", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def send_file_to_user():
    from raven.blueprints.marti_api import data_package_marti_api

    username = request.form.get("username")
    if not username:
        return _error(gettext("Please specify a username"), 400)
    upload = request.files.get("file")
    if upload is None:
        return _error(gettext("Please provide a file"), 400)
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 404)
    euds = list(user.euds)
    if not euds:
        return _error(gettext("%(username)s has no devices enrolled to receive a file", username=username), 400)

    original = upload.filename or ""
    extension = original.rsplit(".", 1)[1] if "." in original else ""
    if not extension and "zip" in (upload.mimetype or ""):
        extension = "zip"
    allowed = [e.lower() for e in app.config.get("ALLOWED_EXTENSIONS") or []]
    if extension.lower() not in allowed:
        return _error(gettext("Invalid file extension: %(extension)s", extension=extension), 400)

    stem = os.path.splitext(secure_filename(original))[0]
    package = db.session.query(DataPackage).filter_by(filename=stem + ".zip").first()
    if package is None:
        try:
            if extension.lower() == "zip":
                file_hash = data_package_marti_api.save_data_package_file(upload, username=current_user.username)
            else:
                file_hash = data_package_marti_api.create_data_package_zip(upload)
        except data_package_marti_api.DataPackageUploadError as e:
            return _error(getattr(e, "message", str(e)), getattr(e, "status", 400))
        package = db.session.query(DataPackage).filter_by(hash=file_hash).first()
        if package is None:
            return _error(gettext("Failed to save the uploaded file"), 500)

    sender_url = "https://{}:{}/Marti/api/sync/metadata/{}/tool".format(
        _hostname(), app.config.get("RAVEN_MARTI_HTTPS_PORT"), package.hash
    )
    node_id = app.config.get("RAVEN_NODE_ID")

    connection = _rabbitmq_connection()
    try:
        channel = connection.channel()
        for eud in euds:
            channel.basic_publish(
                exchange="dms",
                routing_key=eud.uid,
                body=json.dumps({"uid": node_id, "cot": _fileshare_cot(package, eud.callsign, sender_url)}),
            )
    finally:
        connection.close()
    return _ok()


def _hostname():
    return urlparse(request.url_root).hostname


# ------------------------------------------------------------------------------- listings


def _filter_id():
    try:
        return int(request.args.get("filter_id"))
    except (TypeError, ValueError):
        return None


def _in_filter(query, user_model):
    filter_id = _filter_id()
    if filter_id is not None:
        query = query.join(UserFilterMember, UserFilterMember.user_id == user_model.id).filter(
            UserFilterMember.filter_id == filter_id
        )
    return query


@user_api_blueprint.route("/api/users", methods=["GET"])
@admin_view("users")
def get_users():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to manage users"))
    if refused:
        return refused

    from raven.models.user import User

    query = db.session.query(User)
    query = search(query, User, "username")
    query = _in_filter(query, User)

    include_federated = (request.args.get("include_federated") or "").lower() in _TRUTHY
    if not include_federated and not request.args.get("username"):
        federated = sqlalchemy.exists().where(EUD.user_id == User.id, EUD.federated_from.isnot(None))
        query = query.filter(~federated)

    protected = list(app.config.get("RAVEN_PROTECTED_USERNAMES") or [])
    if protected:
        query = query.order_by(sqlalchemy.case((User.username.in_(protected), 0), else_=1))
    return paginate(query, User)


@user_api_blueprint.route("/api/users/all", methods=["GET"])
@admin_view("users", "groups", "device_profiles")
def get_all_users():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to manage users"))
    if refused:
        return refused

    from raven.models.user import User

    query = _in_filter(db.session.query(User), User)
    return jsonify([user.serialize() for user in query.all()])


@user_api_blueprint.route("/api/users/groups", methods=["GET"])
@admin_view("users")
def get_user_groups():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to manage groups"))
    if refused:
        return refused

    username = request.args.get("username")
    if not username:
        return _error(gettext("Please provide a username"), 400)
    username = _clean(username)
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s not found", username=username), 404)

    memberships = (
        db.session.query(GroupUser, Group)
        .join(Group, Group.id == GroupUser.group_id)
        .filter(GroupUser.user_id == user.id)
        .all()
    )
    results = [
        {"group_name": group.name, "direction": membership.direction, "active": bool(membership.enabled)}
        for membership, group in memberships
    ]
    return _ok(results=results)


@user_api_blueprint.route("/api/users/groups", methods=["PUT"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def add_user_to_groups():
    refused = _ldap_guard(gettext("LDAP is enabled, please use your LDAP server to add users to groups"))
    if refused:
        return refused

    body = _json()
    groups = body.get("groups")
    username = body.get("username")
    direction = body.get("direction")
    if not groups or not username or not direction:
        return _error(gettext("Please provide a list of groups, a username, and a direction"), 400)
    if direction not in ("IN", "OUT"):
        return _error(gettext("Direction must be IN or OUT"), 400)

    protected = _protected_user_response(username)
    if protected:
        return protected
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s doesn't exist", username=username), 404)

    for group_name in groups:
        group_name = _clean(group_name)
        group = db.session.query(Group).filter_by(name=group_name).first()
        if group is None:
            return _error(gettext("Group %(group_name)s doesn't exist", group_name=group_name), 404)
        membership = GroupUser()
        membership.user_id = user.id
        membership.group_id = group.id
        membership.direction = direction
        membership.enabled = True
        try:
            db.session.add(membership)
            db.session.commit()
        except sqlalchemy.exc.IntegrityError:
            db.session.rollback()
    return _ok()


# -------------------------------------------------------------------------------- filters


@user_api_blueprint.route("/api/users/filters", methods=["GET"])
@admin_view("users")
def get_user_filters():
    filters = db.session.query(UserFilter).order_by(UserFilter.name).all()
    return jsonify([f.serialize() for f in filters])


@user_api_blueprint.route("/api/users/filters", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def create_user_filter():
    name = _json().get("name")
    if not name:
        return _error(gettext("Please provide a name"), 400)
    name = _clean(name)
    if db.session.query(UserFilter).filter_by(name=name).first():
        return _error(gettext("A filter named %(name)s already exists", name=name), 409)

    user_filter = UserFilter()
    user_filter.name = name
    db.session.add(user_filter)
    db.session.commit()
    return _ok()


def _membership_exists(filter_id, user_id):
    return (
        db.session.query(UserFilterMember)
        .filter(UserFilterMember.filter_id == filter_id, UserFilterMember.user_id == user_id)
        .first()
    )


@user_api_blueprint.route("/api/users/filters", methods=["PUT"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def add_user_to_filter():
    body = _json()
    username = body.get("username")
    filter_name = body.get("filter_name")
    if not username or not filter_name:
        return _error(gettext("Please provide a username and filter_name"), 400)
    username = _clean(username)
    filter_name = _clean(filter_name)

    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 400)

    user_filter = db.session.query(UserFilter).filter_by(name=filter_name).first()
    if user_filter is None:
        user_filter = UserFilter()
        user_filter.name = filter_name
        db.session.add(user_filter)
        db.session.flush()

    if not _membership_exists(user_filter.id, user.id):
        member = UserFilterMember()
        member.filter_id = user_filter.id
        member.user_id = user.id
        db.session.add(member)
    try:
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
    return _ok()


@user_api_blueprint.route("/api/users/filters", methods=["DELETE"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def remove_user_from_filter():
    username = request.args.get("username")
    filter_name = request.args.get("filter_name")
    if not username or not filter_name:
        return _error(gettext("Please provide a username and filter_name"), 400)

    user = _find_user(_clean(username))
    user_filter = db.session.query(UserFilter).filter_by(name=_clean(filter_name)).first()
    if not user or user_filter is None:
        return _error(gettext("Filter or user not found"), 404)

    member = _membership_exists(user_filter.id, user.id)
    if member is not None:
        db.session.delete(member)
        db.session.commit()
    return _ok()


@user_api_blueprint.route("/api/users/filters/<int:filter_id>", methods=["DELETE"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def delete_user_filter(filter_id):
    user_filter = db.session.get(UserFilter, filter_id)
    if user_filter is None:
        return _error(gettext("Filter not found"), 404)

    db.session.query(UserFilterMember).filter(UserFilterMember.filter_id == filter_id).delete(synchronize_session=False)
    db.session.flush()
    db.session.delete(user_filter)
    db.session.commit()
    return _ok()


# ---------------------------------------------------------------------------- Remote Clear


@user_api_blueprint.route("/api/user/clear_content", methods=["POST"])
@auth_required()
@roles_accepted(ADMIN_ROLE)
def clear_user_content():
    refused = _ldap_guard(gettext("LDAP is enabled, please manage users on your LDAP server"))
    if refused:
        return refused

    from raven import remote_clear
    from raven.models.PendingClearCommand import PendingClearCommand

    body = _json()
    username = body.get("username")
    if not username:
        return _error(gettext("Please specify a username"), 400)
    username = _clean(username)
    clearmaps = bool(body.get("clearmaps", False))
    queue_if_offline = bool(body.get("queue_if_offline", False))

    protected = _protected_user_response(username)
    if protected:
        return protected
    user = _find_user(username)
    if not user:
        return _error(gettext("User %(username)s does not exist", username=username), 404)
    euds = list(user.euds)
    if not euds:
        return _error(gettext("This user has no enrolled devices"), 400)

    node_id = app.config.get("RAVEN_NODE_ID")
    sent_now = queued = reconnect_requested = 0
    connection = None
    try:
        if queue_if_offline:
            now = datetime.datetime.now(datetime.timezone.utc)
            for eud in euds:
                command = PendingClearCommand()
                command.eud_uid = eud.uid
                command.clearmaps = clearmaps
                command.requested_by = current_user.username
                command.requested_at = now
                command.node_id = node_id
                db.session.add(command)
                queued += 1
            db.session.commit()

            connection = _rabbitmq_connection()
            channel = connection.channel()
            # Must match the declaration everywhere else or the broker refuses it (406)
            channel.exchange_declare("eud_control", durable=True, exchange_type="direct")
            for eud in euds:
                if eud.last_status != "Connected":
                    continue
                channel.basic_publish(
                    exchange="eud_control",
                    routing_key=eud.uid,
                    body=json.dumps({"action": "disconnect", "uid": eud.uid}),
                    properties=pika.BasicProperties(expiration="10000"),
                )
                reconnect_requested += 1
        else:
            connection = _rabbitmq_connection()
            channel = connection.channel()
            for eud in euds:
                remote_clear.publish_clear_cot(channel, eud.uid, node_id, clearmaps=clearmaps)
                sent_now += 1
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Remote clear for {username} failed: {e}")
        logger.debug(traceback.format_exc())
        return _error(str(e), 500)
    finally:
        if connection is not None:
            try:
                connection.close()
            except BaseException:
                pass

    _audit(
        "security",
        "remote_clear",
        user.username,
        details="Remote clear-content on {} device(s): {} sent, {} queued (clearmaps={})".format(
            len(euds), sent_now, queued, clearmaps
        ),
    )
    return _ok(
        devices=len(euds),
        sent_now=sent_now,
        queued=queued,
        reconnect_requested=reconnect_requested,
        clearmaps=clearmaps,
    )


# --------------------------------------------------------------------- certificate validity


def _validity_json(user):
    maximum = max_cert_days()
    return _ok(
        username=user.username,
        cert_validity_days=user.cert_validity_days,
        effective_days=resolve_cert_days(user),
        default_days=maximum,
        max_days=maximum,
    )


@user_api_blueprint.route("/api/users/certificate_validity", methods=["GET"])
@auth_required()
def get_certificate_validity():
    username = request.args.get("username") or current_user.username
    if not _resolves_to_caller(username) and not _is_admin():
        return _error(gettext("You may only view your own certificate validity"), 403)
    user = _find_user(username)
    if not user:
        return _error(gettext("No such user: %(username)s", username=username), 404)
    return _validity_json(user)


@user_api_blueprint.route("/api/users/certificate_validity", methods=["PUT"])
@auth_required()
def set_certificate_validity():
    if not _is_admin():
        return _error(gettext("Only administrators may change certificate validity"), 403)

    body = _json()
    username = body.get("username")
    if not username:
        return _error(gettext("Please provide a username"), 400)
    user = _find_user(username)
    if not user:
        return _error(gettext("No such user: %(username)s", username=username), 404)

    try:
        days = validate_cert_days(body.get("cert_validity_days"))
    except ValueError:
        return _error(
            gettext("Certificate validity must be a whole number of days from 1 to %(max)s", max=max_cert_days()),
            400,
        )

    user.cert_validity_days = days
    db.session.commit()
    _audit(
        "certificate",
        "cert_validity_changed",
        user.username,
        details="Certificate validity set to {} days".format(days if days is not None else "server default"),
    )
    return _validity_json(user)

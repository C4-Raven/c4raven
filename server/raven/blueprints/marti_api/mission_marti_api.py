"""TAK Server compatible Data Sync (missions) API on the Marti port.

ATAK's Data Sync plugin, WinTAK, iTAK and TAKX use these routes to list, create, change and delete
missions, subscribe to them, invite others, attach files and map items, and read a mission's change
log, CoT, roles and journal. Devices hold a per-mission RS256 token (iTAK, which sends none, is
checked by client certificate instead). Subscribers are told about changes with mission CoT
messages published through RabbitMQ.

The archive/KML/layer/external data/feed routes live in mission_data_marti_api, which registers on
this module's blueprint; it is imported at the bottom of this file.
"""

import datetime
import hashlib
import importlib
import io
import json
import math
import os
import uuid
import zipfile
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, SubElement, fromstring, tostring

import bleach
import flask
import jwt
import pika
import sqlalchemy
from bs4 import BeautifulSoup
from flask import Blueprint, request
from flask import current_app as app
from flask import jsonify
from flask_babel import gettext
from flask_security import hash_password, verify_password
from werkzeug.utils import secure_filename

# Tests patch verify_client_cert on this module; call sites look it up here at call time
from raven.blueprints.marti_api.marti_api import verify_client_cert
from raven.extensions import db, logger
from raven.functions import datetime_from_iso8601_string, iso8601_string_from_datetime
from raven.models.CoT import CoT
from raven.models.EUD import EUD
from raven.models.Group import Group
from raven.models.GroupMission import GroupMission
from raven.models.GroupUser import GroupUser
from raven.models.Mission import Mission
from raven.models.MissionChange import (
    MissionChange,
    generate_mission_change_cot,
    upsert_mission_uid_and_change,
)
from raven.models.MissionContent import MissionContent
from raven.models.MissionContentMission import MissionContentMission
from raven.models.MissionInvitation import MissionInvitation
from raven.models.MissionLogEntry import MissionLogEntry
from raven.models.MissionRole import MissionRole
from raven.models.MissionUID import MissionUID
from raven.models.Team import Team

mission_marti_api = Blueprint("mission_marti_api", __name__)

# Mission package limits. Read at call time (tests lower them).
MAX_MISSION_PACKAGE_ENTRIES = 100000
MAX_MISSION_PACKAGE_ENTRY_BYTES = 10 * 1024 * 1024
MAX_MISSION_PACKAGE_TOTAL_BYTES = 50 * 1024 * 1024


class MissionPackageTooLarge(Exception):
    """A mission package that would make the server decompress too much"""


_VALID_ROLES = {
    MissionRole.MISSION_OWNER,
    MissionRole.MISSION_SUBSCRIBER,
    MissionRole.MISSION_READ_ONLY,
}

_OWNER_PERMISSIONS = (
    "MISSION_MANAGE_FEEDS",
    "MISSION_SET_PASSWORD",
    "MISSION_WRITE",
    "MISSION_MANAGE_LAYERS",
    "MISSION_UPDATE_GROUPS",
    "MISSION_DELETE",
    "MISSION_SET_ROLE",
    "MISSION_READ",
)

_INVITATION_COLUMNS = {
    "clientuid": "client_uid",
    "callsign": "callsign",
    "username": "username",
    "group": "group_name",
    "team": "team_name",
}

_SSL_ONLY = "Missions are only supported on SSL connections"


# --- Small helpers -----------------------------------------------------------------------------


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _envelope(data_type: str, data, status: int = 200, **extra) -> flask.Response:
    body = {
        "version": "3",
        "type": data_type,
        "data": data,
        "nodeId": app.config.get("RAVEN_NODE_ID"),
    }
    body.update(extra)
    return flask.make_response(jsonify(body), status)


def _error(message: str, status: int) -> flask.Response:
    return flask.make_response(jsonify({"success": False, "error": message}), status)


def _empty(status: int = 200) -> flask.Response:
    return flask.make_response("", status)


def _is_itak() -> bool:
    return "iTAK" in (request.user_agent.string or "")


def _is_admin(user) -> bool:
    return bool(user is not None and user.has_role("administrator"))


def _find_user(username: str | None):
    if not username:
        return None
    return app.security.datastore.find_user(username=username)


def _cert_username(cert) -> str | None:
    try:
        return cert.get_subject().commonName
    except Exception:
        return None


def _get_mission(name: str | None) -> Mission | None:
    if not name:
        return None
    return db.session.execute(db.select(Mission).filter_by(name=name)).scalar()


def _get_mission_by_guid(guid: str | None) -> Mission | None:
    if not guid:
        return None
    return db.session.execute(db.select(Mission).filter_by(guid=guid)).scalar()


def _get_eud(uid: str | None) -> EUD | None:
    if not uid:
        return None
    return db.session.execute(db.select(EUD).filter_by(uid=uid)).scalar()


def _role_row(client_uid: str | None, mission_name: str) -> MissionRole | None:
    if not client_uid:
        return None
    return db.session.execute(
        db.select(MissionRole).filter_by(clientUid=client_uid, mission_name=mission_name)
    ).scalar()


def _eud_username(eud: EUD | None) -> str:
    if eud is not None and eud.user is not None and eud.user.username:
        return eud.user.username
    return "anonymous"


def _role_object(role_type: str | None) -> dict:
    """The JSON role object TAK clients expect for a stored role type"""
    if role_type == MissionRole.MISSION_OWNER:
        return MissionRole.OWNER_ROLE
    if role_type == MissionRole.MISSION_SUBSCRIBER:
        return MissionRole.SUBSCRIBER_ROLE
    return MissionRole.READ_ONLY_ROLE


def _default_role_object(mission: Mission) -> dict:
    if mission.default_role == MissionRole.MISSION_OWNER:
        return MissionRole.OWNER_ROLE
    if mission.default_role == MissionRole.MISSION_READ_ONLY:
        return MissionRole.READ_ONLY_ROLE
    return MissionRole.SUBSCRIBER_ROLE


def _visible_changes(mission_name: str) -> list[dict]:
    rows = db.session.execute(
        db.select(MissionChange).filter_by(mission_name=mission_name)
    ).scalars()
    return [change.to_json() for change in rows if not change.hidden_from_marti()]


def _resolve_or_404(mission_name: str | None, mission_guid: str | None):
    """(name, None) or (None, 404 response) for the name/GUID forms of a route"""
    name = _resolve_mission_name(mission_name, mission_guid)
    if not name:
        return None, flask.make_response(*_mission_not_found(mission_guid))
    return name, None


def cert_user_inactive(username: str | None):
    """A 403 answer when `username` is an existing but deactivated account, else None"""
    user = _find_user(username)
    if user is not None and not user.active:
        return jsonify({"success": False, "error": gettext("Account is deactivated")}), 403
    return None


def _cert_user(no_cert_status: int | None, no_cert_message: str | None):
    """(user, None) for a valid client cert naming an active user, else (None, error response).
    no_cert_message None means an empty body for a missing cert."""
    cert = verify_client_cert()
    if not cert:
        if no_cert_message is None:
            return None, _empty(no_cert_status)
        return None, _error(no_cert_message, no_cert_status)
    username = _cert_username(cert)
    user = _find_user(username)
    if user is None:
        return None, _error(gettext("Unknown user certificate"), 403)
    if not user.active:
        return None, _error(gettext("Account is deactivated"), 403)
    return user, None


# --- Mission tokens ----------------------------------------------------------------------------


def _key_path(filename: str) -> str:
    return os.path.join(app.config.get("RAVEN_CA_FOLDER"), "certs", "raven", filename)


def mission_token_lifetime() -> int:
    """Seconds a mission token is valid for: RAVEN_MISSION_TOKEN_DAYS (default 30, minimum 1)"""
    raw = app.config.get("RAVEN_MISSION_TOKEN_DAYS", 30)
    try:
        days = float(raw) if raw else 30.0
    except (TypeError, ValueError):
        days = 30.0
    if not math.isfinite(days):
        days = 30.0
    days = max(days, 1.0)
    return int(days * 86400)


def generate_token(mission: Mission, eud_uid: str) -> str:
    """A mission token (RS256 JWT) for one device. Needs a request context (the issuer is the
    host the request was sent to)."""
    with open(_key_path("raven.nopass.key"), "r") as key_file:
        key = key_file.read()
    issued = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    claims = {
        "jti": str(uuid.uuid4()),
        "iat": issued,
        "sub": eud_uid,
        "iss": urlparse(request.base_url).hostname,
        "MISSION_NAME": mission.name,
        "MISSION_GUID": mission.guid,
        "exp": issued + mission_token_lifetime(),
    }
    return jwt.encode(claims, key, algorithm="RS256")


def _bearer_token() -> str | None:
    for header in ("MissionAuthorization", "Authorization"):
        value = request.headers.get(header)
        if value and "Bearer" in value:
            return value.replace("Bearer ", "").strip()
    return None


def verify_token():
    """The claims of a valid mission token sent with this request, or False"""
    token = _bearer_token()
    if not token:
        return False
    try:
        with open(_key_path("raven.pub"), "r") as key_file:
            public_key = key_file.read()
        claims = jwt.decode(token, public_key, algorithms=["RS256"], options={"verify_aud": False})
    except Exception as e:
        logger.debug(f"Rejected mission token: {e}")
        return False
    return claims if isinstance(claims, dict) else False


# --- Permission checks -------------------------------------------------------------------------


def verify_itak_certificate(mission_name: str | None = None, mission_guid: str | None = None):
    """iTAK sends no mission tokens: its client cert, the creatorUid it names and a role row for
    that device on the mission stand in for one. Returns the MissionRole or a Response."""

    def fail(message, status):
        return _error(message, status)

    cert = verify_client_cert()
    if not cert:
        return fail("Missing or invalid client certificate", 401)
    username = _cert_username(cert)
    user = _find_user(username)
    if user is None:
        return fail(f"User {username} not found", 401)
    if not user.active:
        return fail("Account is deactivated", 403)

    creator_uid = request.args.get("creatorUid")
    if not creator_uid:
        return fail("Invalid creatorUid", 400)
    eud = db.session.execute(db.select(EUD).filter_by(uid=creator_uid, user_id=user.id)).scalar()
    if eud is None:
        return fail(f"User {username} does not own EUD {creator_uid}", 401)

    if not mission_name and mission_guid:
        mission = _get_mission_by_guid(mission_guid)
        if mission is None:
            return fail(f"Invalid mission GUID: {mission_guid}", 404)
        mission_name = mission.name

    role = db.session.execute(
        db.select(MissionRole).filter_by(
            clientUid=eud.uid, username=username, mission_name=mission_name
        )
    ).scalar()
    if role is None:
        return fail("Access Denied", 403)
    return role


def check_permission(mission_name: str | None = None, mission_guid: str | None = None):
    """True, or a Response refusing the request: a mission token for this mission (by name, else
    by GUID), or for iTAK the certificate check. Any role passes; writes check the role too."""
    if _is_itak():
        role = verify_itak_certificate(mission_name, mission_guid)
        if isinstance(role, flask.Response):
            return role
        return True

    if mission_name:
        token = verify_token()
        if not token or token.get("MISSION_NAME") != mission_name:
            return _error("Missing or invalid token", 401)
    elif mission_guid:
        token = verify_token()
        if not token or token.get("MISSION_GUID") != mission_guid:
            return _error("Missing or invalid token", 401)
    return True


def _acting_device(mission_name: str, mission_guid: str | None = None):
    """The owner-type routes' check: a token for this mission (the device is its sub) or the iTAK
    certificate check (the device is the role row's clientUid). Returns the uid or a Response."""
    if _is_itak():
        role = verify_itak_certificate(mission_name, mission_guid)
        if isinstance(role, flask.Response):
            return role
        return role.clientUid
    token = verify_token()
    if not token or token.get("MISSION_NAME") != mission_name:
        return _error(gettext("Missing or invalid token"), 401)
    return token.get("sub")


def _write_permission(mission_name: str, mission_guid: str | None = None):
    """After check_permission: refuses callers whose role on the mission can't write (read-only
    subscribers, or a token whose device no longer has a role row). True or a Response."""
    if _is_itak():
        role = verify_itak_certificate(mission_name, mission_guid)
        if isinstance(role, flask.Response):
            return role
        eud_uid = role.clientUid
    else:
        token = verify_token()
        eud_uid = token.get("sub") if token else None
        role = _role_row(eud_uid, mission_name)
    if role is None:
        logger.warning(f"{eud_uid} has no role on mission {mission_name}")
        return _error(gettext("You are not subscribed to this mission"), 403)
    if "MISSION_WRITE" not in _role_object(role.role_type)["permissions"]:
        logger.warning(f"{eud_uid} ({role.role_type}) may not change mission {mission_name}")
        return _error(
            gettext(
                "Your mission role does not have the %(permission)s permission",
                permission="MISSION_WRITE",
            ),
            403,
        )
    return True


def _check_write(mission_name: str, mission_guid: str | None = None):
    granted = check_permission(mission_name)
    if isinstance(granted, flask.Response):
        return granted
    return _write_permission(mission_name, mission_guid)


def _resolve_mission_name(mission_name: str | None, mission_guid: str | None = None) -> str | None:
    if mission_name:
        return mission_name
    mission = _get_mission_by_guid(mission_guid)
    return mission.name if mission else None


def _mission_not_found(identifier):
    message = gettext("Mission %(mission_name)s not found", mission_name=identifier or "")
    return jsonify({"success": False, "error": message}), 404


# --- RabbitMQ ----------------------------------------------------------------------------------


def _rabbit_connection() -> pika.BlockingConnection:
    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=app.config.get("RAVEN_RABBITMQ_SERVER_ADDRESS", "127.0.0.1"),
            credentials=pika.PlainCredentials(
                app.config.get("RAVEN_RABBITMQ_USERNAME"), app.config.get("RAVEN_RABBITMQ_PASSWORD")
            ),
        )
    )


def _body(event, sender: str | None = None) -> bytes:
    cot = event if isinstance(event, str) else tostring(event).decode()
    return json.dumps(
        {"uid": sender if sender is not None else app.config.get("RAVEN_NODE_ID"), "cot": cot}
    ).encode()


def _publish(
    exchange: str, routing_key: str, event, sender: str | None = None, channel=None
) -> None:
    """Publishes one CoT, on `channel` (left open) or on a connection of its own"""
    if channel is not None:
        channel.basic_publish(exchange=exchange, routing_key=routing_key, body=_body(event, sender))
        return
    connection = _rabbit_connection()
    try:
        connection.channel().basic_publish(
            exchange=exchange, routing_key=routing_key, body=_body(event, sender)
        )
    finally:
        connection.close()


def _notify(exchange: str, routing_key: str, event, sender: str | None = None) -> None:
    """_publish for notifications sent after the database change is committed: a broker failure
    is logged, the change stands and clients pick it up on their next sync"""
    try:
        _publish(exchange, routing_key, event, sender)
    except Exception as e:
        logger.error(f"Failed to publish to {exchange}/{routing_key}: {e}")


def _publish_mission_change_cot(mission_name: str, event, creator_uid: str | None = None) -> None:
    """Sends a mission change CoT to the mission's subscribers. creator_uid is not used."""
    _publish("missions", f"missions.{mission_name}", event)


# --- Mission CoT -------------------------------------------------------------------------------


def _mission_event(cot_type: str, mission: Mission, mission_type: str, hae: str | None = None):
    now = _now()
    event = Element(
        "event",
        {
            "version": "2.0",
            "how": "h-g-i-g-o",
            "uid": str(uuid.uuid4()),
            "type": cot_type,
            "start": iso8601_string_from_datetime(now),
            "time": iso8601_string_from_datetime(now),
            "stale": iso8601_string_from_datetime(now + datetime.timedelta(hours=1)),
        },
    )
    point = {"ce": "9999999", "le": "9999999"}
    if hae is not None:
        point.update({"hae": hae, "lat": hae, "lon": hae})
    else:
        point.update({"hae": "0", "lat": "0", "lon": "0"})
    SubElement(event, "point", point)
    detail = SubElement(event, "detail")
    mission_element = SubElement(
        detail,
        "mission",
        {
            "type": mission_type,
            "tool": mission.tool or "public",
            "name": mission.name,
            "guid": mission.guid or "",
            "authorUid": mission.creator_uid or "",
        },
    )
    return event, mission_element


def generate_new_mission_cot(mission: Mission) -> Element:
    event, _ = _mission_event("t-x-m-n", mission, "CREATE", hae="0")
    return event


def generate_mission_delete_cot(mission: Mission) -> Element:
    event, _ = _mission_event("t-x-m-d", mission, "DELETE")
    return event


def generate_invitation_cot(
    mission: Mission,
    uid: str,
    cot_type: str = "t-x-m-i",
    delete: bool = False,
    role: str | None = None,
) -> Element:
    """An invitation (t-x-m-i) or role change (t-x-m-r) for one device. delete=True leaves the
    role out (the device was removed from the mission)."""
    event, mission_element = _mission_event(cot_type, mission, "INVITE")
    mission_element.set("token", generate_token(mission, uid))
    if not delete:
        role_type = role or mission.default_role or MissionRole.MISSION_SUBSCRIBER
        role_element = SubElement(mission_element, "role", {"type": role_type})
        permissions_element = SubElement(role_element, "permissions")
        if role_type == MissionRole.MISSION_SUBSCRIBER:
            permissions = ("MISSION_READ", "MISSION_WRITE")
        elif role_type == MissionRole.MISSION_OWNER:
            permissions = _OWNER_PERMISSIONS
        else:
            permissions = ("MISSION_READ",)
        for permission in permissions:
            SubElement(permissions_element, "permission", {"type": permission})
    return event


# --- Invitations -------------------------------------------------------------------------------


def _invitation_refusal(message: str, status: int):
    """(response, status): _create_invitation's callers unpack the tuple."""
    return jsonify({"success": False, "error": message}), status


def _row_exists(model, **criteria) -> bool:
    return db.session.execute(db.select(model).filter_by(**criteria)).scalar() is not None


# Invitation type (lower-cased) -> (MissionInvitation column, type name sent back to TAK,
# does-the-invitee-exist check, not-found message)
_INVITEE_KINDS = {
    "clientuid": (
        "client_uid",
        "clientUid",
        lambda who: _get_eud(who) is not None,
        lambda who: gettext("No EUD found with UID %(invitee)s", invitee=who),
    ),
    "callsign": (
        "callsign",
        "callsign",
        lambda who: _row_exists(EUD, callsign=who),
        lambda who: gettext("No EUD found with callsign %(invitee)s", invitee=who),
    ),
    "username": (
        "username",
        "userName",
        lambda who: _find_user(who) is not None,
        lambda who: gettext("No user found with username %(invitee)s", invitee=who),
    ),
    "group": (
        "group_name",
        "group",
        lambda who: _row_exists(Group, name=who),
        lambda who: gettext("No group found: %(invitee)s", invitee=who),
    ),
    "team": (
        "team_name",
        "team",
        lambda who: _row_exists(Team, name=who),
        lambda who: gettext("Team not found: %(invitee)s", invitee=who),
    ),
}


def _create_invitation(
    mission: Mission,
    invitation_type: str,
    invitee: str,
    role: str | None = None,
    creator_uid: str | None = None,
    channel=None,
):
    """Stores an invitation and sends it to the invitee. The caller has already authorised the
    request. Returns None, or a (response, status) tuple to answer with."""
    if not invitation_type or not invitee:
        return _invitation_refusal(gettext("Invalid invitation type"), 400)

    target = _INVITEE_KINDS.get(invitation_type.lower())
    if target is None:
        return _invitation_refusal(
            gettext("Invalid invitation type: %(invitation_type)s", invitation_type=invitation_type), 400
        )
    column, tak_type, exists, not_found = target
    if not exists(invitee):
        return _invitation_refusal(not_found(invitee), 404)

    invitation = MissionInvitation()
    setattr(invitation, column, invitee)
    invitation.type = tak_type
    invitation.mission_name = mission.name
    invitation.mission_guid = mission.guid
    invitation.creator_uid = creator_uid or mission.creator_uid
    invitation.role = (
        MissionRole.normalize_role_type(role)
        or mission.default_role
        or MissionRole.MISSION_SUBSCRIBER
    )
    try:
        db.session.add(invitation)
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
        return _invitation_refusal(gettext("Invalid invitee: %(invitee)s", invitee=invitee), 400)

    event = generate_invitation_cot(mission, invitee, role=invitation.role)
    _publish("dms", invitee, event, channel=channel)
    return None


def _invitation_list(
    mission_name: str | None = None, mission_guid: str | None = None, all_missions=False
):
    user, failure = _cert_user(400, gettext(_SSL_ONLY))
    if failure:
        return failure

    client_uid = request.args.get("clientUid")
    client_uid = bleach.clean(client_uid) if client_uid else None
    if client_uid and not _is_admin(user):
        owned = db.session.execute(
            db.select(EUD).filter_by(uid=client_uid, user_id=user.id)
        ).scalar()
        if owned is None:
            return _error(gettext("Access Denied"), 403)

    query = db.select(MissionInvitation)
    if client_uid:
        query = query.filter_by(client_uid=client_uid)
    if mission_guid:
        mission_name = _resolve_mission_name(None, mission_guid)
        if not mission_name:
            return _envelope("MissionInvitation", [], messages=[])
    if mission_name:
        query = query.filter_by(mission_name=mission_name)
    invitations = db.session.execute(query).scalars().all()

    if all_missions:
        names = []
        for invitation in invitations:
            if invitation.mission_name not in names:
                names.append(invitation.mission_name)
        return _envelope("MissionInvitation", names, messages=[])

    data = []
    missions = {}
    for invitation in invitations:
        token = ""
        if client_uid and invitation.mission_name:
            if invitation.mission_name not in missions:
                missions[invitation.mission_name] = _get_mission(invitation.mission_name)
            mission = missions[invitation.mission_name]
            if mission is not None:
                token = generate_token(mission, client_uid)
        data.append(invitation.to_marti_json(token))
    return _envelope("MissionInvitation", data, messages=[])


@mission_marti_api.route("/Marti/api/missions/invitations", methods=["GET"])
def get_invitations():
    return _invitation_list()


@mission_marti_api.route("/Marti/api/missions/all/invitations", methods=["GET"])
@mission_marti_api.route("/Marti/api/missions/all/invitations/", methods=["GET"])
def get_all_invitations():
    return _invitation_list(all_missions=True)


@mission_marti_api.route("/Marti/api/missions/<mission_name>/invitations", methods=["GET"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/invitations", methods=["GET"])
def get_mission_invitations(mission_name: str = None, mission_guid: str = None):
    return _invitation_list(mission_name, mission_guid)


@mission_marti_api.route(
    "/Marti/api/missions/<mission_name>/invite/<invitation_type>/<invitee>", methods=["PUT"]
)
@mission_marti_api.route(
    "/Marti/api/missions/guid/<mission_guid>/invite/<invitation_type>/<invitee>", methods=["PUT"]
)
def invite(invitation_type: str, invitee: str, mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    allowed = _check_write(name, mission_guid)
    if isinstance(allowed, flask.Response):
        return allowed
    mission = _get_mission(name)
    if mission is None:
        return flask.make_response(*_mission_not_found(name))

    result = _create_invitation(
        mission,
        invitation_type,
        invitee,
        role=request.args.get("role"),
        creator_uid=request.args.get("creatorUid") or mission.creator_uid,
    )
    if result is not None:
        return flask.make_response(*result)
    return _empty()


@mission_marti_api.route(
    "/Marti/api/missions/<mission_name>/invite/<invitation_type>/<invitee>", methods=["DELETE"]
)
@mission_marti_api.route(
    "/Marti/api/missions/guid/<mission_guid>/invite/<invitation_type>/<invitee>", methods=["DELETE"]
)
def uninvite(
    invitation_type: str, invitee: str, mission_name: str = None, mission_guid: str = None
):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    allowed = _check_write(name, mission_guid)
    if isinstance(allowed, flask.Response):
        return allowed
    mission = _get_mission(name)
    if mission is None:
        return flask.make_response(*_mission_not_found(name))

    column = _INVITATION_COLUMNS.get((invitation_type or "").lower())
    if column is None:
        message = gettext(
            "Invalid invitation type: %(invitation_type)s", invitation_type=invitation_type
        )
        return _error(message, 400)

    invitations = db.session.execute(
        db.select(MissionInvitation).filter_by(mission_name=mission.name, **{column: invitee})
    ).scalars()
    for invitation in invitations:
        db.session.delete(invitation)
    db.session.commit()
    return jsonify({"success": True})


@mission_marti_api.route("/Marti/api/missions/<mission_name>/invite", methods=["POST"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/invite", methods=["POST"])
def invite_many(mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    allowed = _check_write(name, mission_guid)
    if isinstance(allowed, flask.Response):
        return allowed

    invitations = request.get_json(silent=True)
    if not isinstance(invitations, list):
        return _error(gettext("Expected a JSON list of invitations"), 400)
    mission = _get_mission(name)
    if mission is None:
        return _error(gettext("Mission not found: %(mission_name)s", mission_name=name), 404)

    creator_uid = request.args.get("creatorUid")
    connection = None
    try:
        for item in invitations:
            if not isinstance(item, dict) or not item.get("type") or not item.get("invitee"):
                return _error(gettext("invitation found without type or invitee attribute"), 400)
            role = item.get("role")
            if isinstance(role, dict):
                role = role.get("type")
            elif not isinstance(role, str):
                role = None
            if connection is None:
                connection = _rabbit_connection()
                channel = connection.channel()
            result = _create_invitation(
                mission, str(item["type"]), str(item["invitee"]), role, creator_uid, channel=channel
            )
            if result is not None:
                return flask.make_response(*result)
    finally:
        if connection is not None:
            connection.close()
    return jsonify({"success": True})


# --- Missions ----------------------------------------------------------------------------------


@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>", methods=["GET"])
def get_mission_by_guid(mission_guid: str):
    granted = check_permission(mission_guid=mission_guid)
    if isinstance(granted, flask.Response):
        return granted
    mission = _get_mission_by_guid(mission_guid)
    if mission is None:
        return _error(
            gettext("No mission found with guid: %(mission_guid)s", mission_guid=mission_guid), 404
        )
    if mission.password_protected:
        password = request.args.get("password")
        if not password or not mission.password or not verify_password(password, mission.password):
            return _error(gettext("Invalid password"), 401)
    logs = request.args.get("logs", "false").lower() == "true"
    return _envelope("Mission", [mission.to_marti_json(logs=logs)])


@mission_marti_api.route("/Marti/api/missions", methods=["GET"])
def get_missions():
    user, failure = _cert_user(401, None)
    if failure:
        return failure

    try:
        password_protected = request.args.get("passwordProtected", "false").lower() == "true"
        tool = bleach.clean(request.args.get("tool", "public"))
        request.args.get("defaultRole")  # accepted, no effect

        query = db.select(Mission)
        if not _is_admin(user):
            in_groups = db.select(GroupUser.group_id).where(
                GroupUser.user_id == user.id, GroupUser.direction == Group.IN
            )
            grouped = db.select(GroupMission.mission_name)
            visible = db.select(GroupMission.mission_name).where(
                GroupMission.group_id.in_(in_groups)
            )
            query = query.where(
                sqlalchemy.or_(Mission.name.not_in(grouped), Mission.name.in_(visible))
            )

        data = []
        for mission in db.session.execute(query).scalars():
            if mission.password_protected and not password_protected:
                continue
            if (mission.tool or "public") != tool:
                continue
            data.append(mission.to_marti_json())
        return _envelope("Mission", data)
    except Exception as e:
        logger.error(f"Failed to list missions: {e}")
        return _error(str(e), 500)


def _owns_mission(user, mission_name: str) -> bool:
    """Whether any of the user's EUDs holds the owner role on the mission"""
    own_euds = db.select(EUD.uid).where(EUD.user_id == user.id)
    owner = db.session.execute(
        db.select(MissionRole).where(
            MissionRole.mission_name == mission_name,
            MissionRole.role_type == MissionRole.MISSION_OWNER,
            MissionRole.clientUid.in_(own_euds),
        )
    ).first()
    return owner is not None


@mission_marti_api.route("/Marti/api/missions/<mission_name>", methods=["PUT", "POST"])
def put_mission(mission_name: str):
    user, failure = _cert_user(400, gettext(_SSL_ONLY))
    if failure:
        return failure

    creator_uid = request.args.get("creatorUid")
    if not mission_name or not creator_uid:
        return _error(gettext("Please provide a mission name and creatorUid"), 400)
    eud = _get_eud(creator_uid)
    if eud is None:
        return _error(gettext("Invalid creatorUid: %(creator_uid)s", creator_uid=creator_uid), 400)
    is_admin = _is_admin(user)
    if eud.user_id != user.id and not is_admin:
        return _error(
            gettext(
                "User %(username)s does not own EUD %(uid)s",
                username=user.username,
                uid=creator_uid,
            ),
            403,
        )

    # One name for every row: the mission's own and the role/change/group rows that refer to it
    name = bleach.clean(mission_name)
    if not name:
        return _error(gettext("Please provide a mission name and creatorUid"), 400)
    mission = _get_mission(name)
    if mission is not None and not is_admin and not _owns_mission(user, name):
        return _error(
            gettext(
                "Only the mission's owner can change mission %(mission_name)s", mission_name=name
            ),
            403,
        )

    # Groups are checked before anything is written, so an unknown one leaves no half-made mission
    group_arg = request.args.get("group")
    groups = []
    if group_arg:
        for group_name in (g.strip() for g in group_arg.split(",")):
            if not group_name:
                continue
            group = db.session.execute(db.select(Group).filter_by(name=group_name)).scalar()
            if group is None:
                return _error(
                    gettext("Group not found: %(group_name)s", group_name=group_name), 404
                )
            groups.append(group)

    created = mission is None
    try:
        if created:
            mission = Mission()
            mission.name = name
            mission.creator_uid = creator_uid
            password = request.args.get("password")
            mission.password = hash_password(password) if password else None
        mission.description = request.args.get("description") or mission.description or None
        mission.tool = request.args.get("tool") or mission.tool or "public"
        mission.group = group_arg or mission.group or "__ANON__"
        mission.default_role = (
            MissionRole.normalize_role_type(request.args.get("defaultRole"))
            or mission.default_role
            or MissionRole.MISSION_SUBSCRIBER
        )
        mission.password_protected = bool(mission.password)
        mission.guid = mission.guid or str(uuid.uuid4())
        mission.create_time = mission.create_time or _now()
        if created:
            db.session.add(mission)
        db.session.commit()

        if groups:
            targets = [group.id for group in groups]
        else:
            anon = db.session.get(Group, 1)
            targets = [anon.id] if anon is not None else []
        for group_id in targets:
            linked = db.session.execute(
                db.select(GroupMission).filter_by(group_id=group_id, mission_name=name)
            ).scalar()
            if linked is None:
                db.session.add(GroupMission(group_id=group_id, mission_name=name))
        db.session.commit()

        if created:
            role = MissionRole()
            role.clientUid = mission.creator_uid
            role.username = _eud_username(eud)
            role.createTime = _now()
            role.role_type = MissionRole.MISSION_OWNER
            role.mission_name = name
            db.session.add(role)

            change = MissionChange()
            change.isFederatedChange = False
            change.change_type = MissionChange.CREATE_MISSION
            change.mission_name = name
            change.timestamp = mission.create_time
            change.server_time = mission.create_time
            change.creator_uid = mission.creator_uid
            db.session.add(change)
            db.session.commit()
    except sqlalchemy.exc.IntegrityError as e:
        # Another request created the same mission at the same moment: answer with that one
        logger.warning(f"Mission {name} already exists: {e}")
        db.session.rollback()
        existing = _get_mission(name)
        if existing is None:
            return _error(gettext("Failed to add mission: %(e)s", e=str(e)), 500)
        return _envelope("Mission", [existing.to_marti_json()])
    except Exception as e:
        logger.error(f"Failed to add mission {name}: {e}")
        db.session.rollback()
        return _error(gettext("Failed to add mission: %(e)s", e=str(e)), 500)

    if created:
        memberships = db.session.execute(
            db.select(GroupUser).filter_by(user_id=user.id, enabled=True)
        ).scalars()
        event = generate_new_mission_cot(mission)
        for membership in memberships:
            group = db.session.get(Group, membership.group_id)
            if group is not None:
                _notify("groups", f"{group.name}.{membership.direction}", event)

    mission_json = mission.to_marti_json()
    mission_json["token"] = generate_token(mission, eud.uid)
    if created:
        mission_json["ownerRole"] = MissionRole.OWNER_ROLE
        return _envelope("Mission", [mission_json], 201)
    return _envelope("Mission", [mission_json], 200)


@mission_marti_api.route("/Marti/api/missions/<mission_name>", methods=["GET"])
def get_mission(mission_name: str):
    name = bleach.clean(mission_name or "")
    if not name:
        return _error(gettext("Invalid mission name"), 400)
    # No mission token here: TAK clients fetch a mission's details (Data Sync preview, password
    # prompt, an expired token) before they hold one. nginx still requires the client cert.
    try:
        mission = _get_mission(name)
        if mission is None:
            return flask.make_response(*_mission_not_found(name))
        logs = request.args.get("logs", "false").lower() == "true"
        return _envelope("Mission", [mission.to_marti_json(logs=logs)])
    except Exception as e:
        logger.error(f"Failed to get mission {name}: {e}")
        return _error(str(e), 400)


@mission_marti_api.route("/Marti/api/missions/<mission_name>", methods=["DELETE"])
@mission_marti_api.route("/Marti/api/missions", methods=["DELETE"])
def delete_mission(mission_name: str = None):
    guid = request.args.get("guid")
    name, failure = _resolve_or_404(mission_name, guid)
    if failure:
        return failure
    mission = _get_mission(name)
    if mission is None:
        return flask.make_response(*_mission_not_found(name))

    device = _acting_device(name, guid)
    if isinstance(device, flask.Response):
        return device
    role = _role_row(device, name)
    if role is None or role.role_type != MissionRole.MISSION_OWNER:
        return _error(gettext("Only mission owners can delete missions"), 403)

    response = _envelope("Mission", [mission.to_marti_json()])
    event = generate_mission_delete_cot(mission)
    db.session.delete(mission)
    db.session.commit()
    _notify("missions", "missions", event)
    return response


@mission_marti_api.route("/Marti/api/missions/<mission_name>/password", methods=["PUT", "DELETE"])
@mission_marti_api.route(
    "/Marti/api/missions/guid/<mission_guid>/password", methods=["PUT", "DELETE"]
)
def mission_password(mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    device = _acting_device(name, mission_guid)
    if isinstance(device, flask.Response):
        return device

    password = request.args.get("password")
    if request.method == "PUT" and password is None:
        return _error(gettext("Please provide the password"), 400)
    role = _role_row(device, name)
    if role is None or role.role_type != MissionRole.MISSION_OWNER:
        return _error(gettext("You do not have permission to change this mission's password"), 403)
    mission = _get_mission(name)
    if mission is None:
        return flask.make_response(*_mission_not_found(name))

    if request.method == "PUT":
        mission.password = hash_password(password)
        mission.password_protected = True
    else:
        mission.password = None
        mission.password_protected = False
    db.session.commit()
    return jsonify({"success": True})


@mission_marti_api.route("/Marti/api/missions/<mission_name>/keywords", methods=["PUT"])
def mission_keywords(mission_name: str):
    allowed = _check_write(mission_name)
    if isinstance(allowed, flask.Response):
        return allowed
    mission = _get_mission(mission_name)
    if mission is None:
        return _error(
            gettext("Cannot find mission %(mission_name)s", mission_name=mission_name), 404
        )
    keywords = list(mission.keywords or [])
    body = request.get_json(silent=True)
    for keyword in body if isinstance(body, list) else []:
        if keyword not in keywords:
            keywords.append(keyword)
    mission.keywords = keywords
    db.session.commit()
    return _empty()


# --- Roles and subscriptions -------------------------------------------------------------------


@mission_marti_api.route("/Marti/api/missions/<mission_name>/subscriptions/roles", methods=["GET"])
@mission_marti_api.route(
    "/Marti/api/missions/guid/<mission_guid>/subscriptions/roles", methods=["GET"]
)
def subscription_roles(mission_name: str = None, mission_guid: str = None):
    granted = check_permission(mission_name, mission_guid)
    if isinstance(granted, flask.Response):
        return granted
    name = _resolve_mission_name(mission_name, mission_guid)
    roles = []
    if name:
        roles = [
            role.to_json()
            for role in db.session.execute(
                db.select(MissionRole).filter_by(mission_name=name)
            ).scalars()
        ]
    return _envelope("MissionSubscription", roles)


@mission_marti_api.route("/Marti/api/missions/<mission_name>/role", methods=["PUT"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/role", methods=["PUT"])
def set_role(mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    device = _acting_device(name, mission_guid)
    if isinstance(device, flask.Response):
        return device
    mission = _get_mission(name)
    if mission is None:
        return _error(gettext("No such mission found: %(mission_name)s", mission_name=name), 404)
    acting_role = _role_row(device, name)
    if acting_role is None or acting_role.role_type != MissionRole.MISSION_OWNER:
        return _error(gettext("Only mission owners can change EUD roles"), 403)

    client_uid = request.args.get("clientUid")
    if not client_uid:
        return _error(gettext("Please provide a UID"), 400)
    eud = _get_eud(client_uid)
    if eud is None:
        return _error(gettext("Invalid UID: %(client_uid)s", client_uid=client_uid), 400)

    new_role = MissionRole.normalize_role_type(request.args.get("role"))
    if new_role and new_role not in _VALID_ROLES:
        return _error(gettext("Invalid role: %(new_role)s", new_role=new_role), 400)

    if new_role:
        for row in db.session.execute(
            db.select(MissionRole).filter_by(clientUid=client_uid, mission_name=name)
        ).scalars():
            db.session.delete(row)
        role = MissionRole()
        role.clientUid = client_uid
        role.username = _eud_username(eud)
        role.createTime = _now()
        role.role_type = new_role
        role.mission_name = name
        db.session.add(role)
        db.session.commit()
        event = generate_invitation_cot(mission, client_uid, "t-x-m-r", role=new_role)
    else:
        existing = _role_row(client_uid, name)
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()
        event = generate_invitation_cot(mission, client_uid, "t-x-m-r", delete=True)
    _notify("dms", client_uid, event)
    return _empty()


@mission_marti_api.route("/Marti/api/missions/<mission_name>/role", methods=["GET"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/role", methods=["GET"])
def get_role(mission_name: str = None, mission_guid: str = None):
    granted = check_permission(mission_name, mission_guid)
    if isinstance(granted, flask.Response):
        return granted
    mission = _get_mission_by_guid(mission_guid) if mission_guid else _get_mission(mission_name)
    if mission is None:
        return flask.make_response(*_mission_not_found(mission_guid or mission_name))

    client_uid = request.args.get("clientUid")
    if not client_uid:
        token = verify_token()
        client_uid = token.get("sub") if token else None
    role = _role_row(client_uid, mission.name)
    data = _role_object(role.role_type) if role is not None else _default_role_object(mission)
    return _envelope("com.bbn.marti.sync.model.MissionRole", data)


@mission_marti_api.route("/Marti/api/missions/<mission_name>/subscriptions", methods=["GET"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/subscriptions", methods=["GET"])
def subscriptions(mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    granted = check_permission(name)
    if isinstance(granted, flask.Response):
        return granted
    roles = db.session.execute(db.select(MissionRole).filter_by(mission_name=name)).scalars()
    return _envelope("MissionSubscription", [role.clientUid for role in roles])


def _ensure_role(mission: Mission, eud: EUD) -> MissionRole:
    role = _role_row(eud.uid, mission.name)
    if role is None:
        role = MissionRole()
        role.clientUid = eud.uid
        role.username = _eud_username(eud)
        role.createTime = _now()
        role.role_type = mission.default_role or MissionRole.MISSION_SUBSCRIBER
        role.mission_name = mission.name
        db.session.add(role)
        db.session.commit()
    return role


def _same_group(user, mission: Mission) -> bool:
    links = (
        db.session.execute(db.select(GroupMission.group_id).filter_by(mission_name=mission.name))
        .scalars()
        .all()
    )
    if not links:
        return True
    member = db.session.execute(
        db.select(GroupUser).where(GroupUser.user_id == user.id, GroupUser.group_id.in_(links))
    ).first()
    return member is not None


@mission_marti_api.route("/Marti/api/missions/<mission_name>/subscription", methods=["PUT"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/subscription", methods=["PUT"])
def subscribe(mission_name: str = None, mission_guid: str = None):
    user, failure = _cert_user(400, gettext(_SSL_ONLY))
    if failure:
        return failure

    if mission_guid:
        mission = _get_mission_by_guid(mission_guid)
        if mission is None:
            return _error(
                gettext("Cannot find mission %(mission_guid)s", mission_guid=mission_guid), 404
            )
    else:
        mission = _get_mission(mission_name)
        if mission is None:
            return _error(
                gettext("Cannot find mission %(mission_name)s", mission_name=mission_name), 404
            )
    name = mission.name

    if not _is_admin(user) and not _same_group(user, mission):
        message = gettext(
            "%(username)s and mission %(mission_name)s are not in the same group",
            username=user.username,
            mission_name=name,
        )
        return _error(message, 403)

    token = verify_token() if _bearer_token() else False
    if token and token.get("sub") and token.get("MISSION_NAME") == name:
        uid = token["sub"]
        eud = _get_eud(uid)
        if eud is None:
            return _error(gettext("Invalid token"), 400)
        role = _ensure_role(mission, eud)
        data = {
            "token": generate_token(mission, uid),
            "clientUid": uid,
            "username": role.username,
            "createTime": iso8601_string_from_datetime(role.createTime),
            "role": _role_object(role.role_type),
        }
    else:
        uid = request.args.get("uid")
        if not uid:
            return _error(gettext("Missing UID"), 400)
        uid = bleach.clean(uid)
        eud = _get_eud(uid)
        if eud is None:
            return _error(gettext("Invalid UID: %(uid)s", uid=uid), 400)
        if eud.user_id != user.id and not _is_admin(user):
            return _error(gettext("Access Denied"), 403)
        if mission.password_protected:
            password = request.args.get("password", "")
            if (
                not password
                or not mission.password
                or not verify_password(password, mission.password)
            ):
                return _error(gettext("Invalid password"), 401)
        role = _ensure_role(mission, eud)
        data = {
            "token": generate_token(mission, uid),
            "clientUid": uid,
            "mission": mission.to_marti_json(logs=True),
            "username": role.username,
            "createTime": iso8601_string_from_datetime(role.createTime),
            "role": _role_object(role.role_type),
        }

    try:
        connection = _rabbit_connection()
        try:
            channel = connection.channel()
            channel.queue_declare(queue=uid)
            channel.queue_bind(exchange="missions", queue=uid, routing_key=f"missions.{name}")
        finally:
            connection.close()
    except Exception as e:
        # The role is saved; the device's queue is bound again on its next subscribe/connect
        logger.error(f"Failed to bind {uid} to mission {name}: {e}")

    stale = db.select(MissionInvitation).filter_by(mission_name=name, client_uid=uid)
    invitations = list(db.session.execute(stale).scalars())
    if eud.callsign:
        invitations += list(
            db.session.execute(
                db.select(MissionInvitation).filter_by(mission_name=name, callsign=eud.callsign)
            ).scalars()
        )
    for invitation in {id(i): i for i in invitations}.values():
        db.session.delete(invitation)
    db.session.commit()

    return _envelope("com.bbn.marti.sync.model.MissionSubscription", data, 201)


@mission_marti_api.route("/Marti/api/missions/<mission_name>/subscription", methods=["GET"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/subscription", methods=["GET"])
def get_subscription(mission_name: str = None, mission_guid: str = None):
    user, failure = _cert_user(400, gettext(_SSL_ONLY))
    if failure:
        return failure
    mission = _get_mission_by_guid(mission_guid) if mission_guid else _get_mission(mission_name)
    if mission is None:
        return flask.make_response(*_mission_not_found(mission_guid or mission_name))

    uid = request.args.get("uid")
    if uid:
        uid = bleach.clean(uid)
        token = verify_token()
        token_ok = bool(
            token
            and token.get("sub") == uid
            and (
                token.get("MISSION_NAME") == mission.name
                or (mission.guid and token.get("MISSION_GUID") == mission.guid)
            )
        )
        owns = (
            db.session.execute(db.select(EUD).filter_by(uid=uid, user_id=user.id)).scalar()
            is not None
        )
        if not token_ok and not owns and not _is_admin(user):
            return _error(gettext("Access Denied"), 403)

    role = _role_row(uid, mission.name) if uid else None
    if role is None:
        return _error(gettext("Mission subscription not found"), 404)
    data = {
        "token": generate_token(mission, uid),
        "clientUid": uid,
        "username": role.username,
        "createTime": iso8601_string_from_datetime(role.createTime),
        "role": _role_object(role.role_type),
    }
    return _envelope("com.bbn.marti.sync.model.MissionSubscription", data)


@mission_marti_api.route("/Marti/api/missions/<mission_name>/subscription", methods=["DELETE"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/subscription", methods=["DELETE"])
def unsubscribe(mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    device = _acting_device(name, mission_guid)
    if isinstance(device, flask.Response):
        return device

    role = _role_row(device, name)
    if role is not None:
        db.session.delete(role)
        db.session.commit()

    if device:
        try:
            connection = _rabbit_connection()
            try:
                connection.channel().queue_unbind(
                    queue=device, exchange="missions", routing_key=f"missions.{name}"
                )
            finally:
                connection.close()
        except Exception as e:
            logger.warning(f"Could not unbind {device} from mission {name}: {e}")
    return _empty()


# --- Changes and log ---------------------------------------------------------------------------


@mission_marti_api.route("/Marti/api/missions/<mission_name>/changes", methods=["GET"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/changes", methods=["GET"])
def mission_changes(mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    granted = check_permission(name)
    if isinstance(granted, flask.Response):
        return granted
    request.args.get("squashed")  # accepted, ignored
    return _envelope("MissionChange", _visible_changes(name))


@mission_marti_api.route("/Marti/api/missions/logs/entries", methods=["POST"])
def add_log_entry():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    names = body.get("missionNames")
    mission_name = (
        names[0] if isinstance(names, list) and names and isinstance(names[0], str) else None
    )
    if not mission_name:
        return _error(gettext("Mission not found: %(mission_name)s", mission_name=""), 404)

    allowed = _check_write(mission_name)
    if isinstance(allowed, flask.Response):
        return allowed
    mission = _get_mission(mission_name)
    if mission is None:
        return _error(
            gettext("Mission not found: %(mission_name)s", mission_name=mission_name), 404
        )

    now = _now()
    entry = MissionLogEntry()
    entry.content = body.get("content")
    entry.creator_uid = body.get("creatorUid")
    entry.entry_uid = str(uuid.uuid4())
    entry.mission_name = mission.name
    entry.server_time = now
    entry.created = now
    entry.dtg = datetime_from_iso8601_string(body.get("dtg"))
    entry.keywords = body.get("keywords")
    db.session.add(entry)
    db.session.commit()

    _notify("missions", f"missions.{mission.name}", entry.generate_cot(), sender=entry.creator_uid)
    return _envelope("com.bbn.marti.sync.model.LogEntry", [entry.to_json()], 201)


@mission_marti_api.route("/Marti/api/missions/<mission_name>/log", methods=["GET"])
def get_log(mission_name: str):
    granted = check_permission(mission_name)
    if isinstance(granted, flask.Response):
        return granted
    mission = _get_mission(mission_name)
    if mission is None:
        return flask.make_response(*_mission_not_found(mission_name))
    entries = db.session.execute(
        db.select(MissionLogEntry).filter_by(mission_name=mission.name)
    ).scalars()
    return _envelope("com.bbn.marti.sync.model.LogEntry", [entry.to_json() for entry in entries])


# --- Content -----------------------------------------------------------------------------------


def _missions_folder() -> str:
    folder = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "missions")
    os.makedirs(folder, exist_ok=True)
    return folder


def _upload_caller():
    """(username or None, None) for a valid cert whose user (if any) is active, else (None, error)"""
    cert = verify_client_cert()
    if not cert:
        return None, _error(gettext("Missing or invalid certificate"), 400)
    username = _cert_username(cert)
    inactive = cert_user_inactive(username)
    if inactive:
        return None, flask.make_response(*inactive)
    return username, None


def _safe_filename(name: str) -> str:
    safe = secure_filename(name)
    base, _ = os.path.splitext(safe)
    if not base:
        extension = os.path.splitext(name)[1].lstrip(".")
        extension = secure_filename(extension)
        safe = uuid.uuid4().hex + (f".{extension}" if extension else "")
    return safe


@mission_marti_api.route("/Marti/sync/upload", methods=["POST"])
def sync_upload():
    username, failure = _upload_caller()
    if failure:
        return failure

    name = request.args.get("name")
    name = bleach.clean(name) if name else ""
    if not name:
        return _error(gettext("File name cannot be blank"), 400)
    keywords = request.args.getlist("keywords")
    creator_uid = request.args.get("creatorUid") or request.args.get("CreatorUid")
    data = request.data
    file_hash = hashlib.sha256(data).hexdigest()

    if _is_itak() and request.content_type == "application/x-zip-compressed":
        # Imported here: the data package module is a sibling blueprint that may import this one
        dp_module = importlib.import_module("raven.blueprints.marti_api.data_package_marti_api")
        try:
            dp_module.save_data_package_file(
                data, secure_filename(name) + ".zip", username, creator_uid
            )
        except dp_module.DataPackageUploadError as e:
            message = getattr(e, "message", None) or str(e)
            status = getattr(e, "status", None) or getattr(e, "status_code", None) or 400
            return _error(message, status)
        return jsonify(
            {
                "UID": str(uuid.uuid4()),
                "SubmissionDateTime": iso8601_string_from_datetime(_now()),
                "MIMEType": "application/x-zip-compressed",
                "SubmissionUser": username,
                "PrimaryKey": 0,
                "Hash": file_hash,
                "CreatorUid": creator_uid,
                "Name": name,
            }
        )

    safe_name = _safe_filename(name)
    extension = os.path.splitext(safe_name)[1]
    allowed = [str(e).lower() for e in app.config.get("ALLOWED_EXTENSIONS") or []]
    if extension.lstrip(".").lower() not in allowed:
        return _error(
            gettext("%(extension)s is not an allowed file extension", extension=extension), 400
        )

    content = db.session.execute(db.select(MissionContent).filter_by(hash=file_hash)).scalar()
    if content is None:
        content = MissionContent()
        content.mime_type = request.content_type
        content.filename = safe_name
        content.submission_time = _now()
        content.submitter = username or "anonymous"
        content.uid = str(uuid.uuid4())
        content.creator_uid = creator_uid
        content.size = request.content_length
        content.expiration = -1
        content.keywords = keywords
        content.hash = file_hash
        db.session.add(content)
    elif content.filename != safe_name:
        content.filename = safe_name
    db.session.commit()

    with open(os.path.join(_missions_folder(), safe_name), "wb") as f:
        f.write(data)

    return jsonify(
        {
            "UID": content.uid,
            "SubmissionDateTime": iso8601_string_from_datetime(content.submission_time),
            "MIMEType": content.mime_type,
            "SubmissionUser": content.submitter,
            "PrimaryKey": content.id,
            "Hash": content.hash,
            "CreatorUid": creator_uid,
            "Name": name,
        }
    )


@mission_marti_api.route("/Marti/api/sync/metadata/<content_hash>/keywords", methods=["PUT"])
def content_keywords(content_hash: str):
    _, failure = _upload_caller()
    if failure:
        return failure
    content = db.session.execute(db.select(MissionContent).filter_by(hash=content_hash)).scalar()
    if content is None:
        return _error(
            gettext("No content found with hash: %(content_hash)s", content_hash=content_hash), 400
        )
    keywords = list(content.keywords or [])
    body = request.get_json(silent=True)
    for keyword in body if isinstance(body, list) else []:
        if keyword not in keywords:
            keywords.append(keyword)
    content.keywords = keywords
    db.session.commit()
    return _empty()


def _event_details(event) -> dict:
    """Map item fields from a CoT <event> (a BeautifulSoup tag): type, callsign, icon, colour,
    position. Missing ones are None."""
    details = {"cot_type": event.get("type"), "callsign": None, "iconset_path": None, "color": None}
    details["latitude"] = details["longitude"] = None
    contact = event.find("contact")
    if contact is not None:
        details["callsign"] = contact.get("callsign")
    icon = event.find("usericon")
    if icon is not None:
        details["iconset_path"] = icon.get("iconsetpath") or icon.get("iconsetPath")
    color = event.find("color")
    if color is not None:
        details["color"] = color.get("argb") or color.get("value")
    point = event.find("point")
    if point is not None:
        try:
            details["latitude"] = float(point.get("lat"))
            details["longitude"] = float(point.get("lon"))
        except (TypeError, ValueError):
            pass
    return details


def _latest_cot(uid: str) -> CoT | None:
    return (
        db.session.execute(
            db.select(CoT).filter_by(uid=uid).order_by(CoT.timestamp.desc(), CoT.id.desc())
        )
        .scalars()
        .first()
    )


def _parse_event(xml: str | None):
    if not xml:
        return None
    try:
        return BeautifulSoup(xml, "xml").find("event")
    except Exception:
        return None


def _content_linked(content: MissionContent, mission_name: str) -> bool:
    return (
        db.session.execute(
            db.select(MissionContentMission).filter_by(
                mission_content_id=content.id, mission_name=mission_name
            )
        ).scalar()
        is not None
    )


def _add_content_change(
    mission: Mission, content: MissionContent, creator_uid: str | None
) -> MissionChange:
    now = _now()
    change = MissionChange()
    change.isFederatedChange = False
    change.change_type = MissionChange.ADD_CONTENT
    change.content_uid = content.uid
    change.mission_name = mission.name
    change.timestamp = now
    change.server_time = now
    change.creator_uid = creator_uid
    db.session.add(change)
    return change


def _has_content_change(mission_name: str, content_uid: str) -> bool:
    return (
        db.session.execute(
            db.select(MissionChange).filter_by(mission_name=mission_name, content_uid=content_uid)
        ).first()
        is not None
    )


@mission_marti_api.route("/Marti/api/missions/<mission_name>/contents", methods=["PUT"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/contents", methods=["PUT"])
def add_contents(mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    allowed = _check_write(name, mission_guid)
    if isinstance(allowed, flask.Response):
        return allowed
    mission = _get_mission(name)
    if mission is None:
        return flask.make_response(*_mission_not_found(name))

    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    hashes = body.get("hashes") or []
    uids = body.get("uids") or []

    # Every hash is checked first so a bad one changes and announces nothing
    contents = []
    for content_hash in hashes:
        content = db.session.execute(
            db.select(MissionContent).filter_by(hash=content_hash)
        ).scalar()
        if content is None:
            return _error(
                gettext("No such file with hash %(content_hash)s", content_hash=content_hash), 404
            )
        contents.append(content)

    events = []
    for content in contents:
        if not _content_linked(content, mission.name):
            db.session.add(
                MissionContentMission(mission_content_id=content.id, mission_name=mission.name)
            )
        if not _has_content_change(mission.name, content.uid):
            change = _add_content_change(mission, content, content.creator_uid)
            db.session.flush()
            events.append(
                generate_mission_change_cot(mission.name, mission, change, content=content)
            )

    creator_uid = request.args.get("creatorUid")
    for uid in uids:
        details = {"cot_type": None, "callsign": None, "iconset_path": None, "color": None}
        details["latitude"] = details["longitude"] = None
        cot = _latest_cot(uid)
        event = _parse_event(cot.xml) if cot is not None else None
        if event is not None:
            details = _event_details(event)
        if cot is not None and not details.get("cot_type"):
            details["cot_type"] = cot.type
        _, _, change_event = upsert_mission_uid_and_change(
            mission.name, mission, uid, creator_uid, _now(), **details
        )
        events.append(change_event)

    db.session.commit()
    for event in events:
        try:
            _publish_mission_change_cot(mission.name, event)
        except Exception as e:
            logger.error(f"Failed to publish a content change for mission {mission.name}: {e}")
    return _envelope("Mission", [mission.to_marti_json()])


@mission_marti_api.route("/Marti/api/missions/<mission_name>/contents", methods=["DELETE"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/contents", methods=["DELETE"])
def remove_contents(mission_name: str = None, mission_guid: str = None):
    name, failure = _resolve_or_404(mission_name, mission_guid)
    if failure:
        return failure
    device = _acting_device(name, mission_guid)
    if isinstance(device, flask.Response):
        return device
    allowed = _write_permission(name, mission_guid)
    if isinstance(allowed, flask.Response):
        return allowed
    mission = _get_mission(name)
    if mission is None:
        return flask.make_response(*_mission_not_found(name))

    uid = request.args.get("uid")
    content_hash = request.args.get("hash")
    now = _now()
    change = MissionChange()
    change.isFederatedChange = False
    change.change_type = MissionChange.REMOVE_CONTENT
    change.mission_name = mission.name
    change.timestamp = now
    change.server_time = now
    change.creator_uid = request.args.get("creatorUid") or device

    cot_event = None
    content = None
    if uid:
        mission_uid = db.session.execute(
            db.select(MissionUID).filter_by(uid=uid, mission_name=mission.name)
        ).scalar()
        if mission_uid is None:
            return _error(gettext("UID %(uid)s not found", uid=uid), 404)
        mission_uid.mission_name = None
        change.mission_uid = uid
        for cot in db.session.execute(
            db.select(CoT).filter_by(uid=uid, mission_name=mission.name)
        ).scalars():
            cot.mission_name = None
        latest = _latest_cot(uid)
        cot_event = _parse_event(latest.xml) if latest is not None else None
    if content_hash:
        content = db.session.execute(
            db.select(MissionContent).filter_by(hash=content_hash)
        ).scalar()
        if content is None:
            return _error(gettext("No content found with hash %(hash)s", hash=content_hash), 404)
        change.content_uid = content.uid
        try:
            for link in db.session.execute(
                db.select(MissionContentMission).filter_by(
                    mission_content_id=content.id, mission_name=mission.name
                )
            ).scalars():
                db.session.delete(link)
            db.session.flush()
        except Exception as e:
            db.session.rollback()
            message = gettext(
                "Failed to delete content with hash %(hash)s: %(e)s", hash=content_hash, e=str(e)
            )
            return _error(message, 500)

    db.session.add(change)
    db.session.commit()
    event = generate_mission_change_cot(
        mission.name, mission, change, content=content, cot_event=cot_event
    )
    try:
        _publish_mission_change_cot(mission.name, event)
    except Exception as e:
        logger.error(f"Failed to publish a content removal for mission {mission.name}: {e}")
    return _envelope("Mission", [mission.to_marti_json()])


# --- Mission packages --------------------------------------------------------------------------


def _parse_mission_package_map_items(file: bytes):
    """(manifest uid or None, [(item uid, <event> tag), ...]) for the .cot entries a mission
    package's manifest lists. Raises MissionPackageTooLarge rather than inflate oversize entries."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(file))
        members = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        return None, []

    if len(members) > MAX_MISSION_PACKAGE_ENTRIES:
        raise MissionPackageTooLarge(
            f"Mission package has too many files (more than {MAX_MISSION_PACKAGE_ENTRIES})"
        )

    budget = [MAX_MISSION_PACKAGE_TOTAL_BYTES]

    def read(member: zipfile.ZipInfo) -> bytes:
        if member.file_size > MAX_MISSION_PACKAGE_ENTRY_BYTES or member.file_size > budget[0]:
            raise MissionPackageTooLarge(f"Mission package entry {member.filename} is too large")
        # Never trust the declared size alone: stop reading one byte past it
        with archive.open(member) as handle:
            data = handle.read(member.file_size + 1)
        if len(data) > member.file_size:
            raise MissionPackageTooLarge(f"Mission package entry {member.filename} is too large")
        budget[0] -= len(data)
        return data

    manifest = next((m for m in members if m.filename.endswith("manifest.xml")), None)
    if manifest is None:
        return None, []

    try:
        manifest_xml = BeautifulSoup(read(manifest), "xml")
    except MissionPackageTooLarge:
        raise
    except Exception:
        return None, []

    manifest_uid = None
    parameter = manifest_xml.find("Parameter", attrs={"name": "uid"})
    if parameter is not None:
        manifest_uid = parameter.get("value")

    by_name = {m.filename: m for m in members}
    items = []
    for content in manifest_xml.find_all("Content"):
        if str(content.get("ignore", "")).lower() == "true":
            continue
        entry = content.get("zipEntry")
        if not entry or not entry.lower().endswith(".cot") or entry not in by_name:
            continue
        try:
            event = BeautifulSoup(read(by_name[entry]), "xml").find("event")
        except MissionPackageTooLarge:
            raise
        except Exception:
            continue
        if event is not None and event.get("uid"):
            items.append((event.get("uid"), event))
    return manifest_uid, items


def _event_time(event, attribute: str) -> datetime.datetime:
    try:
        return (
            datetime_from_iso8601_string(event.get(attribute)) if event.get(attribute) else _now()
        )
    except (TypeError, ValueError):
        return _now()


def _save_package_cot(event, mission: Mission, creator_uid: str | None) -> None:
    uid = event.get("uid")
    sender_uid = creator_uid if _get_eud(creator_uid) is not None else None
    cot = _latest_cot(uid)
    if cot is None:
        cot = CoT()
        cot.uid = uid
    cot.how = event.get("how")
    cot.type = event.get("type")
    cot.sender_uid = sender_uid
    cot.timestamp = _event_time(event, "time")
    cot.start = _event_time(event, "start")
    cot.stale = _event_time(event, "stale")
    cot.xml = str(event)
    cot.mission_name = mission.name
    db.session.add(cot)


@mission_marti_api.route(
    "/Marti/api/missions/<mission_name>/contents/missionpackage", methods=["PUT"]
)
def upload_mission_package(mission_name: str):
    username, failure = _upload_caller()
    if failure:
        return failure
    creator_uid = request.args.get("creatorUid") or request.args.get("clientUid")
    mission = _get_mission(mission_name)
    if mission is None:
        return _error(gettext("No such mission: %(mission_name)s", mission_name=mission_name), 404)

    data = request.data
    try:
        manifest_uid, items = _parse_mission_package_map_items(data)
    except MissionPackageTooLarge as e:
        return _error(str(e), 413)

    file_hash = hashlib.sha256(data).hexdigest()
    content = db.session.execute(db.select(MissionContent).filter_by(hash=file_hash)).scalar()
    if content is None:
        content = MissionContent()
        content.mime_type = request.content_type or "application/x-zip-compressed"
        content.filename = f"{mission.name}_{uuid.uuid4().hex}.zip"
        content.submission_time = _now()
        content.submitter = username or "anonymous"
        taken = (
            manifest_uid
            and db.session.execute(db.select(MissionContent).filter_by(uid=manifest_uid)).first()
        )
        content.uid = manifest_uid if manifest_uid and not taken else str(uuid.uuid4())
        content.creator_uid = creator_uid
        content.size = request.content_length
        content.expiration = -1
        content.keywords = []
        content.hash = file_hash
        db.session.add(content)
        db.session.flush()
    with open(
        os.path.join(_missions_folder(), secure_filename(content.filename) or content.filename),
        "wb",
    ) as f:
        f.write(data)
    if not _content_linked(content, mission.name):
        db.session.add(
            MissionContentMission(mission_content_id=content.id, mission_name=mission.name)
        )
    db.session.commit()

    events = []
    if items:
        for item_uid, event in items:
            details = _event_details(event)
            _, change, change_event = upsert_mission_uid_and_change(
                mission.name, mission, item_uid, creator_uid, _now(), **details
            )
            change.content_uid = content.uid
            _save_package_cot(event, mission, creator_uid)
            events.append(change_event)
        db.session.commit()
    elif not _has_content_change(mission.name, content.uid):
        change = _add_content_change(mission, content, creator_uid)
        db.session.commit()
        events.append(generate_mission_change_cot(mission.name, mission, change, content=content))

    for event in events:
        try:
            _publish_mission_change_cot(mission.name, event)
        except Exception as e:
            logger.error(f"Failed to publish a mission package change for {mission.name}: {e}")
    return _envelope("MissionChange", _visible_changes(mission.name))


@mission_marti_api.route("/Marti/api/missions/<mission_name>/cot", methods=["GET"])
@mission_marti_api.route("/Marti/api/missions/guid/<mission_guid>/cot", methods=["GET"])
def mission_cot(mission_name: str = None, mission_guid: str = None):
    granted = check_permission(mission_name, mission_guid)
    if isinstance(granted, flask.Response):
        return granted
    name = _resolve_mission_name(mission_name, mission_guid)
    events = Element("events")
    if name:
        for cot in db.session.execute(db.select(CoT).filter_by(mission_name=name)).scalars():
            try:
                events.append(fromstring(cot.xml))
            except Exception as e:
                logger.warning(f"Skipping unreadable CoT {cot.uid} in mission {name}: {e}")
    return flask.Response(tostring(events), status=200, mimetype="application/xml")


# The archive, KML, layer, external data and feed routes register on mission_marti_api
from raven.blueprints.marti_api import mission_data_marti_api  # noqa: E402, F401

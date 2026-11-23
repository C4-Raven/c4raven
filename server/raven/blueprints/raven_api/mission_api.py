"""The web UI's Data Sync (mission) management: list the missions a user may see, create or edit
one (groups, password, and the CoT that tells devices about it), delete one with all its rows, and
invite a device. The mission detail page and downloads are in mission_detail_api.py, which
registers on this blueprint."""

import datetime
import json
import traceback
import uuid
from xml.etree.ElementTree import tostring

import bleach
import pika
import sqlalchemy.exc
from flask import Blueprint, current_app as app, jsonify, request
from flask_babel import gettext
from flask_security import auth_required, current_user, hash_password, roles_required, verify_password
from sqlalchemy import select

from raven.blueprints.raven_api.api import paginate, search
from raven.extensions import db, logger
from raven.models.Chatrooms import Chatroom
from raven.models.ChatroomsUids import ChatroomsUids
from raven.models.CoT import CoT
from raven.models.EUD import EUD
from raven.models.GeoChat import GeoChat
from raven.models.Group import Group
from raven.models.GroupMission import GroupMission
from raven.models.GroupUser import GroupUser
from raven.models.Mission import Mission
from raven.models.MissionChange import MissionChange
from raven.models.MissionContentMission import MissionContentMission
from raven.models.MissionExternalData import MissionExternalData
from raven.models.MissionFeed import MissionFeed
from raven.models.MissionInvitation import InvitationTypeEnum, MissionInvitation
from raven.models.MissionLayer import MissionLayer
from raven.models.MissionLogEntry import MissionLogEntry
from raven.models.MissionRole import MissionRole
from raven.models.MissionUID import MissionUID

data_sync_api = Blueprint("data_sync_api", __name__)

# What a user who may not open a mission doesn't get in the mission list
REDACTED_KEYS = ("contents", "uids", "missionChanges", "externalData", "feeds")

# Columns a client may set by name. Relationships aren't settable through this route, and
# password_protected follows the password.
_DERIVED = ("password", "password_protected")


def _settable_columns() -> set:
    return {c.key for c in Mission.__mapper__.column_attrs} - set(_DERIVED)


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _is_admin(user=None) -> bool:
    user = user or current_user
    return bool(user and user.is_authenticated and user.has_role("administrator"))


def _owns_eud(user, uid) -> bool:
    if not uid:
        return False
    return db.session.execute(select(EUD.id).where(EUD.user_id == user.id, EUD.uid == uid)).first() is not None


def _publish(messages):
    """Publishes [(exchange, routing key, CoT element)] as the server node, on one connection."""
    if not messages:
        return
    connection = None
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(
                host=app.config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"),
                credentials=pika.PlainCredentials(
                    app.config.get("RAVEN_RABBITMQ_USERNAME"), app.config.get("RAVEN_RABBITMQ_PASSWORD")
                ),
            )
        )
        channel = connection.channel()
        for exchange, routing_key, event in messages:
            # The node id, never the creator's uid: EUD handlers drop messages carrying their own uid
            body = json.dumps({"uid": app.config.get("RAVEN_NODE_ID"), "cot": tostring(event).decode()})
            channel.basic_publish(exchange=exchange, routing_key=routing_key, body=body)
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _parse_group_ids(value):
    """A list of ints, or None when it isn't one."""
    if not isinstance(value, list):
        return None
    ids = []
    for item in value:
        if isinstance(item, bool):
            return None
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            return None
    return ids


# --------------------------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------------------------


@data_sync_api.route("/api/missions", methods=["GET"])
@auth_required()
def get_missions():
    query = db.session.query(Mission)
    for field in ("name", "guid", "tool"):
        query = search(query, Mission, field)

    admin = _is_admin()
    if not admin:
        in_groups = select(GroupUser.group_id).where(
            GroupUser.user_id == current_user.id, GroupUser.direction == Group.IN
        )
        if db.session.execute(in_groups.limit(1)).first() is not None:
            # A subquery rather than join + DISTINCT so sorting by name works on Postgres
            query = query.filter(
                Mission.name.in_(select(GroupMission.mission_name).where(GroupMission.group_id.in_(in_groups)))
            )

    response = paginate(query, Mission)
    if admin or isinstance(response, tuple):
        return response
    body = response.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        return response

    for row in body["results"]:
        mission = db.session.execute(select(Mission).filter_by(name=row.get("name"))).scalars().first()
        if mission is None or not mission_detail_api._can_view(mission, current_user):
            for key in REDACTED_KEYS:
                row[key] = []
    return jsonify(body)


# --------------------------------------------------------------------------------------------
# Create / edit
# --------------------------------------------------------------------------------------------


@data_sync_api.route("/api/missions", methods=["PUT", "POST"])
@auth_required()
def create_edit_mission():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    if not data.get("name") or not data.get("creator_uid"):
        return _error("Please provide a mission name and creator UID", 400)
    name = bleach.clean(str(data["name"]))
    creator_uid = bleach.clean(str(data["creator_uid"]))

    group_ids = None
    if "groups" in data and data["groups"] is not None:
        group_ids = _parse_group_ids(data["groups"])
        if group_ids is None:
            return _error(gettext("Invalid group id"), 400)

    mission = db.session.execute(select(Mission).filter_by(name=name)).scalars().first()
    if mission is None:
        return _create_mission(data, name, creator_uid, group_ids)
    return _edit_mission(mission, data, group_ids)


def _create_mission(data, name, creator_uid, group_ids):
    from raven.blueprints.marti_api.mission_marti_api import generate_invitation_cot, generate_new_mission_cot

    if db.session.execute(select(EUD).filter_by(uid=creator_uid)).scalars().first() is None:
        return _error(f"Invalid UID: {creator_uid}", 400)

    admin = _is_admin()
    columns = _settable_columns()
    mission = Mission()
    mission.create_time = _now()
    mission.guid = str(uuid.uuid4())
    group_rows, group_names = [], []

    for key, value in data.items():
        if key == "password" and value:
            mission.password = hash_password(str(value))
        elif key == "password":
            mission.password = None
        elif key == "groups":
            for group_id in group_ids or []:
                group = db.session.get(Group, group_id)
                if not admin:
                    member = db.session.execute(
                        select(GroupUser).where(
                            GroupUser.user_id == current_user.id,
                            GroupUser.group_id == group_id,
                            GroupUser.direction == Group.IN,
                            GroupUser.enabled.is_(True),
                        )
                    ).first()
                    if member is None:
                        if group is not None:
                            return _error(f"User is not a member of {group.name}", 403)
                        return _error(f"Invalid group ID: {group_id}", 400)
                if group is None:
                    continue
                if any(existing == group.id for existing, _ in group_rows):
                    continue
                group_rows.append((group.id, group.name))
                group_names.append(group.name)
        elif key in columns:
            setattr(mission, key, value)
        else:
            return _error(gettext("Invalid property: %(key)s", key=key), 400)

    mission.name = name
    mission.creator_uid = creator_uid
    mission.password_protected = bool(mission.password)
    if not mission.tool:
        # Data Sync clients ignore missions that aren't "public"
        mission.tool = "public"
    if not mission.default_role:
        mission.default_role = MissionRole.MISSION_SUBSCRIBER

    try:
        db.session.add(mission)
        role = MissionRole()
        role.clientUid = creator_uid
        role.username = current_user.username
        role.createTime = _now()
        role.role_type = MissionRole.MISSION_OWNER
        role.mission_name = name
        db.session.add(role)

        invitation = MissionInvitation()
        invitation.mission_name = name
        invitation.client_uid = creator_uid
        invitation.creator_uid = creator_uid
        invitation.role = MissionRole.MISSION_OWNER
        invitation.type = InvitationTypeEnum.clientUid.value
        db.session.add(invitation)
        db.session.commit()

        for group_id, _ in group_rows:
            db.session.add(GroupMission(mission_name=name, group_id=group_id))
        db.session.commit()
    except sqlalchemy.exc.IntegrityError as e:
        db.session.rollback()
        return _error(gettext("Failed to add mission: %(e)s", e=str(e)), 400)

    messages = [("groups", f"{group_name}.OUT", generate_new_mission_cot(mission)) for group_name in group_names]
    messages.append(("dms", creator_uid, generate_invitation_cot(mission, creator_uid)))
    try:
        _publish(messages)
    except Exception as e:
        logger.error(f"Failed to announce mission {name}: {e}")
        logger.debug(traceback.format_exc())
    return jsonify({"success": True})


def _edit_mission(mission, data, group_ids):
    if not _is_admin() and not _owns_eud(current_user, mission.creator_uid):
        return _error(gettext("Only an admin or the mission creator can change this mission"), 403)

    columns = _settable_columns() - {"name"}
    for key, value in data.items():
        if key == "password":
            if value:
                mission.password = hash_password(str(value))
                mission.password_protected = True
            else:
                mission.password = None
                mission.password_protected = False
        elif key == "groups":
            if not group_ids:
                continue
            db.session.query(GroupMission).filter_by(mission_name=mission.name).delete()
            db.session.commit()
            for group_id in dict.fromkeys(group_ids):
                if db.session.get(Group, group_id) is not None:
                    db.session.add(GroupMission(mission_name=mission.name, group_id=group_id))
            db.session.commit()
        elif key == "name":
            continue
        elif key in columns:
            setattr(mission, key, bleach.clean(value) if key == "creator_uid" and isinstance(value, str) else value)
        else:
            db.session.rollback()
            return _error(gettext("Invalid property: %(key)s", key=key), 400)

    if not mission.tool:
        mission.tool = "public"
    db.session.commit()
    return jsonify({"success": True})


# --------------------------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------------------------


@data_sync_api.route("/api/missions", methods=["DELETE"])
@roles_required("administrator")
def delete_mission():
    from raven.blueprints.marti_api.mission_marti_api import generate_mission_delete_cot

    name = request.args.get("name")
    if not name:
        return _error(gettext("Please specify a mission name"), 404)
    name = bleach.clean(name)
    mission = db.session.execute(select(Mission).filter_by(name=name)).scalars().first()
    if mission is None:
        return _error(gettext("Mission %(mission_name)s not found", mission_name=name), 404)

    event = generate_mission_delete_cot(mission)
    try:
        session = db.session
        session.query(GroupMission).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(MissionContentMission).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(MissionChange).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(MissionExternalData).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(MissionFeed).filter_by(mission_name=name).delete(synchronize_session=False)
        # Children before the folders they're in
        session.query(MissionLayer).filter(
            MissionLayer.mission_name == name, MissionLayer.parent_uid.isnot(None)
        ).delete(synchronize_session=False)
        session.query(MissionLayer).filter_by(mission_name=name).delete(synchronize_session=False)
        for chatroom in session.query(Chatroom).filter_by(name=name).all():
            session.query(ChatroomsUids).filter_by(chatroom_id=chatroom.id).delete(synchronize_session=False)
            session.query(GeoChat).filter_by(chatroom_id=chatroom.id).delete(synchronize_session=False)
            session.delete(chatroom)
        session.query(CoT).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(MissionInvitation).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(MissionLogEntry).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(MissionRole).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(MissionUID).filter_by(mission_name=name).delete(synchronize_session=False)
        session.query(Mission).filter_by(name=name).delete(synchronize_session=False)
        session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to delete mission {name}: {e}")
        logger.debug(traceback.format_exc())
        return _error(str(e), 500)

    try:
        _publish([("missions", "missions", event)])
    except Exception as e:
        logger.error(f"Failed to announce the deletion of mission {name}: {e}")
    return jsonify({"success": True})


# --------------------------------------------------------------------------------------------
# Invite
# --------------------------------------------------------------------------------------------


@data_sync_api.route("/api/missions/invite", methods=["POST"])
@auth_required()
def invite_eud():
    from raven.blueprints.marti_api.mission_marti_api import _create_invitation

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get("mission_name") or not data.get("uid"):
        return _error("Please provide a mission_name and uid", 400)
    mission_name = bleach.clean(str(data["mission_name"]))
    uid = bleach.clean(str(data["uid"]))
    mission = db.session.execute(select(Mission).filter_by(name=mission_name)).scalars().first()
    if mission is None:
        return _error(gettext("Mission not found: %(mission_name)s", mission_name=mission_name), 404)

    if mission.password_protected and not _is_admin():
        password = data.get("password")
        if not password:
            return _error(gettext("Please provide the mission password"), 403)
        if not mission.password or not verify_password(str(password), mission.password):
            return _error(gettext("Invalid password"), 401)

    result = _create_invitation(mission, "clientuid", uid)
    if result is not None:
        return result
    return jsonify({"success": True})


# Registers the detail page routes on data_sync_api (it imports data_sync_api from here)
from raven.blueprints.raven_api import mission_detail_api  # noqa: E402

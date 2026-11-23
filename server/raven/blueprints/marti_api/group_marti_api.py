"""TAK group/channel endpoints: the caller's groups, toggling active channels (rebinding the device's
RabbitMQ queue), LDAP group lookups and a few TAK-compatible stubs.

verify_client_cert is looked up through this module at call time so tests can replace it.
"""

import datetime

import bleach
import pika
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from sqlalchemy import select

from raven.blueprints.marti_api.marti_api import verify_client_cert
from raven.extensions import db, ldap_manager, logger
from raven.models.EUD import EUD
from raven.models.Group import Group
from raven.models.GroupUser import GroupUser

group_api = Blueprint("group_api", __name__)

_ANON = "__ANON__"
_ANON_BITPOS = 2


def _node_id():
    return app.config.get("RAVEN_NODE_ID")


def _envelope(data_type, data, **extra):
    body = {"version": "3", "type": data_type, "data": data, "nodeId": _node_id()}
    body.update(extra)
    return jsonify(body)


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


def _ldap_enabled() -> bool:
    return bool(app.config.get("RAVEN_ENABLE_LDAP"))


def _cert_username():
    """The CN of the caller's verified client cert, or None (a malformed header counts as none)."""
    try:
        cert = verify_client_cert()
    except BaseException as e:
        logger.debug(f"Unreadable client certificate: {e}")
        return None
    if not cert:
        return None
    return cert.get_subject().commonName


def _anon_entries():
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    entry = {
        "name": _ANON,
        "created": created,
        "type": Group.SYSTEM,
        "bitpos": _ANON_BITPOS,
        "active": True,
        "description": "",
    }
    return [dict(entry, direction=Group.OUT), dict(entry, direction=Group.IN)]


def _transient_ldap_group(info: dict) -> Group:
    """A Group (never added to the session) describing an LDAP group"""
    group = Group()
    group.name = _first(info.get("cn")) or ""
    group.distinguishedName = info.get("dn")
    group.type = Group.LDAP
    group.description = _first(info.get("description")) or ""
    group.created = datetime.datetime.now(datetime.timezone.utc)
    return group


def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _ldap_group_info(group_name: str):
    from ldap3.utils.conv import escape_filter_chars

    object_filter = app.config.get("LDAP_GROUP_OBJECT_FILTER") or "(objectclass=group)"
    return ldap_manager.get_object(
        ldap_manager.full_group_search_dn,
        f"(&{object_filter}(cn={escape_filter_chars(group_name)}))",
        ["cn", "description", "member"],
    )


def _check_group_name_filter():
    """The cleaned groupNameFilter, or an error response tuple"""
    group_name_filter = request.args.get("groupNameFilter")
    if not group_name_filter:
        return None, _error(gettext("Please specify a groupNameFilter"), 400)
    group_name_filter = bleach.clean(group_name_filter)

    prefix = app.config.get("RAVEN_LDAP_GROUP_PREFIX") or ""
    if not group_name_filter.startswith(prefix):
        return None, _error(gettext("Please specify a groupNameFilter that starts with %(prefix)s", prefix=prefix), 400)
    if not (group_name_filter.endswith("_READ") or group_name_filter.endswith("_WRITE")):
        return None, _error(gettext("groupNameFilter must end with either _READ or _WRITE"), 400)
    return group_name_filter, None


def _rabbitmq_connection():
    credentials = pika.PlainCredentials(
        app.config.get("RAVEN_RABBITMQ_USERNAME"), app.config.get("RAVEN_RABBITMQ_PASSWORD")
    )
    return pika.BlockingConnection(
        pika.ConnectionParameters(host=app.config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"), credentials=credentials)
    )


@group_api.route("/Marti/api/groups/groupCacheEnabled", methods=["GET"])
def group_cache_enabled():
    return _envelope("java.lang.Boolean", bool(app.config.get("RAVEN_ENABLE_CHANNELS")))


@group_api.route("/Marti/api/groups/all", methods=["GET"])
def get_all_groups():
    cert = verify_client_cert()
    if not cert:
        return _error(gettext("Groups are only supported on SSL connections"), 400)
    username = cert.get_subject().commonName

    data = []
    if _ldap_enabled():
        prefix = app.config.get("RAVEN_LDAP_GROUP_PREFIX")
        user_info = ldap_manager.get_user_info_for_username(username)
        groups = ldap_manager.get_user_groups(dn=user_info["dn"]) if user_info else []
        for info in groups or []:
            cn = _first(info.get("cn")) or ""
            if prefix and not cn.startswith(prefix):
                continue
            if cn.lower().endswith("_write"):
                data.append(_transient_ldap_group(info).to_marti_json_in())
            elif cn.lower().endswith("_read"):
                data.append(_transient_ldap_group(info).to_marti_json_out())
    else:
        user = app.security.datastore.find_user(username=username)
        if user is None:
            logger.warning(f"No user named {username} for the groups request")
        else:
            memberships = db.session.execute(select(GroupUser).filter_by(user_id=user.id)).scalars()
            for membership in memberships:
                if membership.direction == Group.IN:
                    data.append(membership.group.to_marti_json_in())
                else:
                    data.append(membership.group.to_marti_json_out())

    if not data and not _ldap_enabled():
        data = _anon_entries()

    return _envelope("com.bbn.marti.remote.groups.Group", data)


@group_api.route("/Marti/api/groups", methods=["GET"])
def get_ldap_groups():
    group_name_filter, error = _check_group_name_filter()
    if error:
        return error

    data = []
    if _ldap_enabled():
        info = _ldap_group_info(group_name_filter)
        if info:
            group = _transient_ldap_group(info)
            entry = group.to_marti_json_in() if group_name_filter.endswith("_WRITE") else group.to_marti_json_out()
            entry["distinguishedName"] = info.get("dn")
            data.append(entry)

    return _envelope("com.bbn.marti.remote.groups.LdapGroup", data)


@group_api.route("/Marti/api/groups/members", methods=["GET"])
def get_ldap_group_members():
    group_name_filter, error = _check_group_name_filter()
    if error:
        return error

    count = 0
    if _ldap_enabled():
        try:
            info = _ldap_group_info(group_name_filter)
            members = (info or {}).get("member") or []
            count = len(members) if isinstance(members, (list, tuple)) else 1
        except BaseException as e:
            logger.error(f"Failed to look up the members of {group_name_filter}: {e}")

    return _envelope("java.lang.Integer", count)


@group_api.route("/Marti/api/groupprefix", methods=["GET"])
def get_group_prefix():
    prefix = app.config.get("RAVEN_LDAP_GROUP_PREFIX") if _ldap_enabled() else ""
    return _envelope("java.lang.String", prefix)


@group_api.route("/Marti/api/groups/activebits", methods=["PUT"])
def put_active_bits():
    request.args.get("clientUid")
    request.json
    return "", 200


def _memberships(user) -> list[tuple[GroupUser, str]]:
    """(membership, group name) for each of the user's memberships; without any, the two __ANON__
    memberships (not in the session yet)"""
    rows = db.session.execute(select(GroupUser).filter_by(user_id=user.id)).scalars()
    memberships = [(membership, membership.group.name) for membership in rows]
    if memberships:
        return memberships

    anon = db.session.execute(select(Group).filter_by(name=_ANON)).scalar_one()
    return [
        (GroupUser(user_id=user.id, group_id=anon.id, direction=direction, enabled=True), anon.name)
        for direction in (Group.IN, Group.OUT)
    ]


@group_api.route("/Marti/api/groups/active", methods=["PUT"])
def put_active_groups():
    username = _cert_username()
    if not username:
        return _error(gettext("Groups are only supported on SSL connections"), 400)

    user = app.security.datastore.find_user(username=username)
    if user is None:
        return _error(gettext("Unknown user certificate"), 403)

    client_uid = request.args.get("clientUid")
    if client_uid:
        uids = [client_uid]
    else:
        # CloudTAK doesn't send a clientUid: every device of the account
        uids = [eud.uid for eud in db.session.execute(select(EUD).filter_by(user_id=user.id)).scalars()]

    items = request.get_json(silent=True)
    if not isinstance(items, list):
        items = []

    connection = _rabbitmq_connection()
    channel = connection.channel()
    memberships = _memberships(user)
    local = not _ldap_enabled()

    for item in items:
        item = item if isinstance(item, dict) else {}
        direction = item.get("direction")
        active = item.get("active")
        group_name = item.get("name")

        error = None
        if not direction:
            error = _error(gettext("Direction must be IN or OUT"), 400)
        elif not isinstance(active, bool):
            error = _error(gettext("The active attribute must be true or false"), 400)
        elif not group_name:
            error = _error(gettext("Group name is required"), 400)
        if error:
            db.session.rollback()
            connection.close()
            return error

        group_name = bleach.clean(group_name)
        matched = False
        for membership, membership_group_name in memberships:
            if membership_group_name != group_name or membership.direction != direction:
                continue
            matched = True
            if local:
                membership.enabled = active
                db.session.add(membership)
            routing_key = f"{group_name}.{direction}"
            for uid in uids:
                if active:
                    channel.queue_declare(queue=uid)
                    channel.queue_bind(exchange="groups", queue=uid, routing_key=routing_key)
                else:
                    channel.queue_unbind(exchange="groups", queue=uid, routing_key=routing_key)

        if not matched:
            db.session.rollback()
            connection.close()
            return _error(
                gettext("%(username)s is not in the %(group_name)s group", username=username, group_name=group_name),
                403,
            )

    try:
        connection.close()
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to update group subscriptions for {username}: {e}")
        return _error(
            gettext("Failed to update group subscriptions for %(username)s: %(e)s", username=username, e=str(e)),
            400,
        )
    return "", 200


@group_api.route("/Marti/api/groups/update/<username>", methods=["GET"])
def update_groups(username):
    return jsonify({"version": "", "type": "", "data": True, "messages": [""], "nodeId": _node_id()})


@group_api.route("/Marti/api/groups/<group_name>/<direction>", methods=["GET"])
def get_group(group_name, direction):
    if not group_name or not direction:
        return _error(gettext("Please provide a group name and direction"), 400)

    if _ldap_enabled():
        info = _ldap_group_info(group_name)
        group = _transient_ldap_group(info) if info else None
    else:
        group = db.session.execute(select(Group).filter_by(name=group_name)).scalar()
        if group is None:
            return _error(
                gettext("No group found: %(group_name)s, %(direction)s", group_name=group_name, direction=direction),
                404,
            )

    data = {}
    if group is not None:
        if direction == Group.IN:
            data = group.to_marti_json_in()
        elif direction == Group.OUT:
            data = group.to_marti_json_out()

    return _envelope("com.bbn.marti.remote.groups.Group", data)


@group_api.route("/Marti/api/subscriptions/all", methods=["GET"])
def get_all_subscriptions():
    return jsonify({"version": "3", "type": "SubscriptionInfo", "data": [], "messages": [], "nodeId": _node_id()})


@group_api.route("/Marti/api/groups/activeForce", methods=["GET"])
def active_force():
    """Raven has no "active force" concept; answer with an empty, active group record."""
    if not request.args.get("username"):
        return "", 400
    placeholder = dict.fromkeys(("name", "distinguishedName", "direction", "created", "description"), "")
    placeholder.update(bitpos=0, active=True, type=Group.LDAP if _ldap_enabled() else Group.SYSTEM)
    return jsonify(placeholder)


@group_api.route("/Marti/api/groups/user", methods=["GET"])
def get_group_user():
    if not request.args.get("username"):
        return "", 400
    return jsonify({"version": "", "type": "", "data": {}, "messages": [""], "nodeId": _node_id()})

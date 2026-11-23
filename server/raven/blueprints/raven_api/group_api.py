"""Groups, memberships, group policies and the "Who Can See Whom" user-visibility diagram.

Memberships are GroupUser rows, one per direction: IN means the user may send into the group,
OUT means they receive from it. Pairwise groups ("Link: alice and bob") are created and removed
only by the visibility diagram; the ordinary group routes hide them and refuse to touch them.
The policy logic shared with user creation and the expiry job lives in raven/group_policy.py.
"""

import datetime
import html
import traceback

import bleach
import sqlalchemy.exc
from flask import Blueprint, current_app as app, jsonify, request
from flask_babel import gettext
from flask_security import auth_required, current_user, roles_required
from sqlalchemy import func, or_, select

from raven import group_policy
from raven.admin_view import admin_view
from raven.audit import record_audit_event
from raven.blueprints.raven_api.api import paginate, search
from raven.extensions import db, ldap_manager, logger
from raven.group_policy import (
    AUDIT_CATEGORY,
    MAX_MEMBERSHIP_DAYS,
    PAIRWISE_PREFIX,
    access_label,
    access_of,
    pairwise_group_description,
    pairwise_group_name,
)
from raven.models.DataPackageGroup import DataPackageGroup
from raven.models.Group import ACCESS_DIRECTIONS, Group
from raven.models.GroupUser import GroupUser

__all__ = ["group_api", "PAIRWISE_PREFIX"]

group_api = Blueprint("group_api", __name__)

POLICY_KEYS = ("default_access", "auto_join", "default_membership_days", "locked")


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _error(message, status=400, **extra):
    body = {"success": False, "error": message}
    body.update(extra)
    return jsonify(body), status


def _ldap_enabled() -> bool:
    return bool(app.config.get("RAVEN_ENABLE_LDAP"))


def _ldap_refusal(message=None):
    return _error(message or gettext("LDAP is enabled. Please view and edit groups on your LDAP server"))


def _user_model():
    from raven.models.user import User

    return User


def _find_user(username):
    if not username:
        return None
    User = _user_model()
    return db.session.execute(select(User).filter_by(username=username)).scalars().first()


def _find_group(name):
    """Looks a group up by the name as sent, HTML-unescaped, then bleach-cleaned (first hit wins).

    Older code stored names bleach-cleaned, so "R&D" may be stored as "R&amp;D"; newer names are
    stored raw. Trying each spelling finds either."""
    if name is None:
        return None
    name = str(name)
    for candidate in dict.fromkeys((name, html.unescape(name), bleach.clean(name))):
        group = db.session.execute(select(Group).filter_by(name=candidate)).scalars().first()
        if group is not None:
            return group
    return None


def _is_pairwise(name) -> bool:
    return isinstance(name, str) and (
        name.startswith(PAIRWISE_PREFIX) or html.unescape(name).startswith(PAIRWISE_PREFIX)
    )


def _pairwise_refusal(action):
    return _error(
        gettext(
            "This group is managed by the user visibility diagram -- disconnect the users there "
            "instead of %(action)s here",
            action=action,
        )
    )


def _locked_refusal(group):
    return _error(
        gettext(
            "%(group_name)s is locked. Unlock it in the group's policies before changing its members " "or deleting it",
            group_name=group.name,
        ),
        403,
    )


def _is_protected(username) -> bool:
    protected = app.config.get("RAVEN_PROTECTED_USERNAMES") or []
    if isinstance(protected, str):
        protected = [p for p in protected.split(",")]
    # Exact names: "Admin" (the system account) is protected, an operator's "admin" is not
    protected = {str(p).strip() for p in protected if str(p).strip()}
    return bool(username) and str(username) in protected


def _protected_refusal(username):
    return _error(gettext("%(username)s is a protected system account and can't be modified", username=username), 403)


def _parse_access(value):
    """ "both" (any case), "IN" or "OUT" (any case) -> the canonical form, else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.lower() == "both":
        return "both"
    if value.upper() in ("IN", "OUT"):
        return value.upper()
    return None


def _parse_expiry(value):
    """(naive UTC datetime or None, error response or None)."""
    try:
        expires_at = group_policy.parse_expires_at(value)
    except (ValueError, TypeError):
        return None, _error(gettext("expires_at must be an ISO 8601 date and time"))
    if expires_at is not None and expires_at <= group_policy.utcnow():
        return None, _error(gettext("The expiry must be in the future"))
    return expires_at, None


def _expiry_label(value) -> str:
    if value is None:
        return "never"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _actor():
    return current_user.username if current_user and current_user.is_authenticated else None


def _audit(action, details, username=None):
    record_audit_event(AUDIT_CATEGORY, action, username=username, actor=_actor(), details=details)


def _validate_policy(data: dict):
    """Validates the policy keys present in `data`. Returns (cleaned values, error response)."""
    cleaned = {}
    if "default_access" in data:
        access = _parse_access(data.get("default_access"))
        if access is None:
            return None, _error(gettext("default_access must be both, IN or OUT"))
        cleaned["default_access"] = access
    for key in ("auto_join", "locked"):
        if key in data:
            value = data.get(key)
            if not isinstance(value, bool):
                return None, _error(gettext("%(key)s must be true or false", key=key))
            cleaned[key] = value
    if "default_membership_days" in data:
        value = data.get("default_membership_days")
        if value is None or value == "" or (type(value) is int and value == 0):
            cleaned["default_membership_days"] = None
        elif type(value) is int and 1 <= value <= MAX_MEMBERSHIP_DAYS:
            cleaned["default_membership_days"] = value
        else:
            return None, _error(
                gettext(
                    "default_membership_days must be a whole number of days from 1 to %(max)s, or empty "
                    "for no expiry",
                    max=MAX_MEMBERSHIP_DAYS,
                )
            )
    return cleaned, None


def _describe_policy(values: dict) -> str:
    parts = []
    for key, value in values.items():
        label = key.replace("_", " ")
        label = label[:1].upper() + label[1:]
        if key == "default_access":
            text = access_label(value)
        elif key == "default_membership_days":
            text = f"{value} days" if value else "never expire"
        elif key == "description":
            text = value if value else "(none)"
        else:
            text = str(value)
        parts.append(f"{label}: {text}")
    return " · ".join(parts)


def _membership_rows(user, group):
    return list(db.session.execute(select(GroupUser).filter_by(user_id=user.id, group_id=group.id)).scalars())


def _only_group_counts(group) -> dict:
    """{"data packages": n} for the items whose only group is `group` (empty when none)."""
    single = (
        select(DataPackageGroup.data_package_id)
        .group_by(DataPackageGroup.data_package_id)
        .having(func.count(DataPackageGroup.group_id) == 1)
    )
    count = db.session.execute(
        select(func.count())
        .select_from(DataPackageGroup)
        .where(DataPackageGroup.group_id == group.id, DataPackageGroup.data_package_id.in_(single))
    ).scalar()
    return {"data packages": count} if count else {}


def _orphan_refusal(group):
    orphaned = _only_group_counts(group)
    if not orphaned:
        return None
    summary = ", ".join(f"{n} {kind[:-1] if n == 1 else kind}" for kind, n in orphaned.items())
    return _error(
        gettext(
            "%(group_name)s is the only group of %(summary)s. Deleting it would make them visible to "
            "everyone -- give them another group first",
            group_name=group.name,
            summary=summary,
        ),
        409,
        orphaned_items=orphaned,
    )


# --- output sanitising ---

_SAFE_USER_KEYS = ("id", "username")


def _safe_mission(mission: dict) -> dict:
    # Mission.serialize() carries the mission's password hash; nobody gets that.
    return {k: v for k, v in (mission or {}).items() if k != "password"}


def _group_json(group: Group, full: bool) -> dict:
    """Group.to_json() without mission password hashes. Without `full` (non-admins), members are
    reduced to their usernames: no emails, login IPs, devices or memberships."""
    data = group.to_json()
    data["missions"] = [_safe_mission(m) for m in data.get("missions") or []]
    if not full:
        data["users"] = [{k: u.get(k) for k in _SAFE_USER_KEYS} for u in data.get("users") or []]
    return data


def _not_pairwise():
    return ~Group.name.startswith(PAIRWISE_PREFIX, autoescape=True)


# --------------------------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------------------------


@group_api.route("/api/groups", methods=["GET"])
@admin_view("groups", "users")
def get_groups():
    if _ldap_enabled():
        return _ldap_refusal()
    query = db.session.query(Group).filter(_not_pairwise())
    # Group has no "active" column; filtering on it used to raise a 500, so it is ignored.
    for field in ("name", "type", "bitpos"):
        query = search(query, Group, field)
    response = paginate(query, Group)
    if isinstance(response, tuple):
        return response
    body = response.get_json(silent=True)
    if not isinstance(body, dict) or "results" not in body:
        return response
    for result in body["results"]:
        if isinstance(result, dict):
            result["missions"] = [_safe_mission(m) for m in result.get("missions") or []]
    return jsonify(body)


def _ldap_groups_for_current_user():
    prefix = (app.config.get("RAVEN_LDAP_GROUP_PREFIX") or "raven_").lower()
    info = ldap_manager.get_user_info_for_username(current_user.username) or {}
    dn = info.get("dn")
    if not dn:
        return []

    def first(entry, key):
        for k, v in entry.items():
            if k.lower() == key:
                if isinstance(v, (list, tuple)):
                    return v[0] if v else None
                return v
        return None

    groups = []
    for entry in ldap_manager.get_user_groups(dn=dn) or []:
        cn = first(entry, "cn")
        if not cn:
            continue
        cn = str(cn)
        lowered = cn.lower()
        if not lowered.startswith(prefix) or lowered.endswith("_read") or lowered.endswith("_write"):
            continue
        group = Group()
        group.id = first(entry, "entryuuid")
        group.name = cn
        group.distinguishedName = first(entry, "dn") or entry.get("dn")
        group.type = Group.LDAP
        groups.append(group)
    return groups


@group_api.route("/api/groups/all", methods=["GET"])
@auth_required()
def get_all_groups():
    is_admin = current_user.has_role("administrator")
    if _ldap_enabled():
        try:
            groups = _ldap_groups_for_current_user()
        except BaseException as e:
            logger.error(f"Failed to read LDAP groups: {e}")
            logger.debug(traceback.format_exc())
            groups = []
        return jsonify([_group_json(g, is_admin) for g in groups])

    if is_admin:
        groups = db.session.execute(select(Group).where(_not_pairwise()).order_by(Group.id)).scalars().all()
    else:
        out_groups = select(GroupUser.group_id).where(
            GroupUser.user_id == current_user.id, GroupUser.direction == Group.OUT
        )
        groups = (
            db.session.execute(select(Group).where(Group.id.in_(out_groups), _not_pairwise()).order_by(Group.id))
            .scalars()
            .all()
        )
    return jsonify([_group_json(g, is_admin) for g in groups])


# --------------------------------------------------------------------------------------------
# Members
# --------------------------------------------------------------------------------------------


@group_api.route("/api/groups/members", methods=["GET"])
@admin_view("groups", "users")
def get_group_members():
    if _ldap_enabled():
        return _ldap_refusal()
    name = request.args.get("name")
    if not name:
        return _error("Please specify a group name")
    group = _find_group(name)
    if group is None:
        return _error(f"Group {bleach.clean(name)} not found", 404)
    rows = db.session.execute(select(GroupUser).filter_by(group_id=group.id)).scalars().all()
    return jsonify(
        [
            {
                "username": row.user.username if row.user else None,
                "direction": row.direction,
                "active": row.enabled,
                "expires_at": row.expires_at_iso(),
            }
            for row in rows
        ]
    )


@group_api.route("/api/groups/members", methods=["DELETE"])
@roles_required("administrator")
def remove_user_from_group():
    if _ldap_enabled():
        return _ldap_refusal()
    username = request.args.get("username")
    group_name = request.args.get("group_name")
    direction = request.args.get("direction")
    if not username or not group_name or not direction:
        return _error(gettext("Please provide the username, group name, and direction"))
    username = bleach.clean(username)
    direction = bleach.clean(direction)

    if _is_pairwise(group_name):
        return _pairwise_refusal_remove()
    if _is_protected(username):
        return _protected_refusal(username)
    access = _parse_access(direction)
    if access is None:
        return _error(gettext("Invalid direction: %(direction)s", direction=direction))
    user = _find_user(username)
    if user is None:
        return _error(gettext("User %(username)s not found", username=username), 404)
    group = _find_group(group_name)
    if group is None:
        return _error(gettext("Group %(group_name)s not found", group_name=bleach.clean(group_name)), 404)
    if group.locked:
        return _locked_refusal(group)

    removed = []
    try:
        for row in _membership_rows(user, group):
            if row.direction in ACCESS_DIRECTIONS[access]:
                removed.append(row.direction)
                db.session.delete(row)
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to remove {username} from {group.name}: {e}")
        logger.debug(traceback.format_exc())
        return _error(
            gettext(
                "Failed to remove %(username)s from %(group_name)s: %(e)s",
                username=username,
                group_name=group.name,
                e=str(e),
            ),
            500,
        )

    if removed:
        group_policy.unbind_euds(user, group.name, removed)
        if _membership_rows(user, group):
            _audit(
                "member_access_changed",
                f"Group: {group.name} · Removed: {access_label(access_of(removed))}",
                username=user.username,
            )
        else:
            _audit("member_removed", f"Group: {group.name}", username=user.username)
    return jsonify({"success": True})


def _pairwise_refusal_remove():
    return _error(
        gettext(
            "This group is managed by the user visibility diagram -- disconnect the users there instead of "
            "removing one side of it here"
        )
    )


@group_api.route("/api/groups/members", methods=["PATCH"])
@roles_required("administrator")
def update_group_member():
    if _ldap_enabled():
        return _ldap_refusal()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    raw_username = data.get("username")
    raw_group_name = data.get("group_name")
    if not raw_username or not raw_group_name:
        return _error(gettext("Please provide the username and group name"))
    username = bleach.clean(str(raw_username))
    group_name = str(raw_group_name)
    if "access" not in data and "expires_at" not in data:
        return _error(gettext("Nothing to change: provide access and/or expires_at"))
    if _is_pairwise(group_name):
        return _pairwise_refusal(gettext("changing it"))
    if _is_protected(username):
        return _protected_refusal(username)

    new_access = None
    if "access" in data:
        new_access = _parse_access(data.get("access"))
        if new_access is None:
            return _error(gettext("access must be both, IN or OUT"))
    expiry_given = "expires_at" in data
    new_expiry = None
    if expiry_given:
        new_expiry, error = _parse_expiry(data.get("expires_at"))
        if error:
            return error

    user = _find_user(username)
    if user is None:
        return _error(gettext("User %(username)s not found", username=username), 404)
    group = _find_group(group_name)
    if group is None:
        return _error(gettext("Group %(group_name)s not found", group_name=bleach.clean(group_name)), 404)
    if group.locked:
        return _locked_refusal(group)

    rows = _membership_rows(user, group)
    if not rows:
        return _error(
            gettext("%(username)s is not a member of %(group_name)s", username=user.username, group_name=group.name),
            404,
        )

    old_access = access_of(row.direction for row in rows)
    expiries = [row.expires_at for row in rows if row.expires_at is not None]
    old_expiry = min(expiries) if expiries else None
    target_access = new_access or old_access

    changes = []
    deleted = []
    try:
        if target_access != old_access:
            group_policy.add_membership(user, group, target_access, new_expiry if expiry_given else old_expiry)
            wanted = ACCESS_DIRECTIONS[target_access]
            for row in rows:
                if row.direction not in wanted:
                    deleted.append(row.direction)
                    db.session.delete(row)
            changes.append(f"Access: {access_label(old_access)} → {access_label(target_access)}")
        if expiry_given:
            expiry_changed = False
            for row in rows:
                if row.direction in deleted:
                    continue
                if row.expires_at != new_expiry:
                    expiry_changed = True
                row.expires_at = new_expiry
            if expiry_changed or old_expiry != new_expiry:
                changes.append(f"Expires: {_expiry_label(old_expiry)} → {_expiry_label(new_expiry)}")
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to update {username} in {group.name}: {e}")
        logger.debug(traceback.format_exc())
        return _error(
            gettext(
                "Failed to update %(username)s in %(group_name)s: %(e)s",
                username=username,
                group_name=group.name,
                e=str(e),
            ),
            500,
        )

    if deleted:
        group_policy.unbind_euds(user, group.name, deleted)
    if changes:
        _audit("member_access_changed", " · ".join([f"Group: {group.name}"] + changes), username=user.username)
    return jsonify({"success": True})


# --------------------------------------------------------------------------------------------
# Groups and policies
# --------------------------------------------------------------------------------------------


@group_api.route("/api/groups", methods=["POST"])
@roles_required("administrator")
def add_group():
    if _ldap_enabled():
        return _ldap_refusal(gettext("LDAP is enabled, please use your LDAP server to add groups"))
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    raw_name = data.get("name")
    name = bleach.clean(str(raw_name)).strip() if raw_name is not None else ""
    if not name:
        return _error(gettext("Missing name"))
    if name.startswith(PAIRWISE_PREFIX) or _is_pairwise(str(raw_name).strip()):
        return _error(gettext("Group names starting with %(prefix)s are reserved", prefix=PAIRWISE_PREFIX))
    description = data.get("description")
    description = bleach.clean(str(description)) if description else None

    policy, error = _validate_policy(data)
    if error:
        return error

    if _find_group(name) is not None:
        return _error(gettext("%(name)s group already exists", name=name))

    try:
        group = Group()
        group.name = name
        group.type = Group.SYSTEM
        group.description = description
        group.created = datetime.datetime.now(datetime.timezone.utc)
        group.default_access = policy.get("default_access", "both")
        group.auto_join = policy.get("auto_join", False)
        group.default_membership_days = policy.get("default_membership_days")
        group.locked = policy.get("locked", False)
        db.session.add(group)
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to add group {name}: {e}")
        logger.debug(traceback.format_exc())
        return _error(gettext("Failed to add %(name)s group: %(e)s", name=name, e=str(e)), 500)

    details = f"Group: {name}"
    given = {k: policy[k] for k in POLICY_KEYS if k in policy}
    if given:
        details += " · " + _describe_policy(given)
    _audit("group_created", details)
    return jsonify({"success": True})


@group_api.route("/api/groups/policy", methods=["PATCH"])
@roles_required("administrator")
def update_group_policy():
    if _ldap_enabled():
        return _ldap_refusal()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    group_name = data.get("group_name")
    if not group_name:
        return _error(gettext("Missing group name"))
    group_name = str(group_name)
    if _is_pairwise(group_name):
        return _pairwise_refusal(gettext("changing it"))
    group = _find_group(group_name)
    if group is None:
        return _error(gettext("No such group: %(group_name)s", group_name=bleach.clean(group_name)), 404)
    if _is_pairwise(group.name):
        return _pairwise_refusal(gettext("changing it"))

    policy, error = _validate_policy(data)
    if error:
        return error
    if "description" in data:
        description = data.get("description")
        policy["description"] = bleach.clean(str(description)) if description else None

    changed = {}
    for key in ("description",) + POLICY_KEYS:
        if key not in policy:
            continue
        current = getattr(group, key)
        if key == "default_access":
            current = current or "both"
        elif key in ("auto_join", "locked"):
            current = bool(current)
        if current != policy[key]:
            setattr(group, key, policy[key])
            changed[key] = policy[key]

    if changed:
        try:
            db.session.commit()
        except BaseException as e:
            db.session.rollback()
            logger.error(f"Failed to change the policies of {group.name}: {e}")
            return _error(str(e), 500)
        _audit("group_policy_changed", f"Group: {group.name} · {_describe_policy(changed)}")
    return jsonify({"success": True, "group": _group_json(group, True)})


@group_api.route("/api/groups/policy/apply", methods=["POST"])
@roles_required("administrator")
def apply_group_auto_join():
    if _ldap_enabled():
        return _ldap_refusal()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    group_name = data.get("group_name")
    if not group_name:
        return _error(gettext("Missing group name"))
    group_name = str(group_name)
    if _is_pairwise(group_name):
        return _pairwise_refusal(gettext("changing it"))
    group = _find_group(group_name)
    if group is None:
        return _error(gettext("No such group: %(group_name)s", group_name=bleach.clean(group_name)), 404)
    if group.locked:
        return _locked_refusal(group)

    User = _user_model()
    access = group.default_access or "both"
    expires_at = group_policy.default_expiry(group)
    members = select(GroupUser.user_id).where(GroupUser.group_id == group.id)
    candidates = db.session.execute(select(User).where(User.id.not_in(members)).order_by(User.username)).scalars().all()
    added = []
    try:
        for user in candidates:
            if not user.active or _is_protected(user.username):
                continue
            if group_policy.add_membership(user, group, access, expires_at):
                added.append(user.username)
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to add users to {group.name}: {e}")
        logger.debug(traceback.format_exc())
        return _error(gettext("Failed to add users to %(group_name)s: %(e)s", group_name=group.name, e=str(e)), 500)

    for username in added:
        _audit(
            "member_added",
            f"Group: {group.name} · Access: {access_label(access)} · Expires: {_expiry_label(expires_at)} · "
            "Applied to existing users",
            username=username,
        )
    return jsonify({"success": True, "added": added})


@group_api.route("/api/groups", methods=["PUT"])
@roles_required("administrator")
def add_user_to_group():
    if _ldap_enabled():
        return _ldap_refusal(gettext("LDAP is enabled, please use your LDAP server to add users to groups"))
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    users = data.get("users")
    group_name = data.get("group_name")
    direction = data.get("direction")
    if users is None or group_name is None or direction is None:
        return _error(gettext("Please provide a list of users, group name, and direction"))
    if not isinstance(users, list):
        return _error(gettext("users must be a list of usernames"))
    access = _parse_access(direction)
    if access is None:
        return _error(gettext("Direction must be IN, OUT or BOTH"))
    group_name = str(group_name)
    if _is_pairwise(group_name):
        return _pairwise_refusal(gettext("changing it"))
    group = _find_group(group_name)
    if group is None:
        return _error(gettext("Group %(group_name)s does not exist", group_name=bleach.clean(group_name)))
    if _is_pairwise(group.name):
        return _pairwise_refusal(gettext("changing it"))
    if group.locked:
        return _locked_refusal(group)

    if "expires_at" in data:
        expires_at, error = _parse_expiry(data.get("expires_at"))
        if error:
            return error
    else:
        expires_at = group_policy.default_expiry(group)

    for username in users:
        username = bleach.clean(str(username)) if username is not None else ""
        user = _find_user(username)
        if user is None:
            return _error(gettext("User %(username)s does not exist", username=username))
        added = group_policy.add_membership(user, group, access, expires_at)
        if not added:
            continue
        try:
            db.session.commit()
        except sqlalchemy.exc.IntegrityError:
            db.session.rollback()
            continue
        _audit(
            "member_added",
            f"Group: {group.name} · Access: {access_label(access_of(added))} · Expires: {_expiry_label(expires_at)}",
            username=user.username,
        )
    return jsonify({"success": True})


@group_api.route("/api/groups", methods=["DELETE"])
@roles_required("administrator")
def delete_group():
    if _ldap_enabled():
        return _ldap_refusal(gettext("LDAP is enabled, please use your LDAP server to delete groups"))
    group_name = request.args.get("group_name")
    if not group_name:
        return _error(gettext("Missing group name"))
    if bleach.clean(group_name) == "__ANON__" or group_name == "__ANON__":
        return _error(gettext("The __ANON__ group cannot be deleted"))
    if _is_pairwise(group_name):
        return _error(
            gettext(
                "This group is managed by the user visibility diagram -- disconnect the users there instead of "
                "deleting it directly"
            )
        )
    group = _find_group(group_name)
    if group is None:
        return _error(gettext("No such group: %(group_name)s", group_name=bleach.clean(group_name)), 404)
    if group.name == "__ANON__":
        return _error(gettext("The __ANON__ group cannot be deleted"))
    if group.locked:
        return _locked_refusal(group)
    refusal = _orphan_refusal(group)
    if refusal:
        return refusal

    name = group.name
    rows = db.session.execute(select(GroupUser).filter_by(group_id=group.id)).scalars().all()
    # user id -> (user, directions); keyed by id because the models are unhashable dataclasses
    former = {}
    for row in rows:
        entry = former.setdefault(row.user_id, (row.user, []))
        entry[1].append(row.direction)
    try:
        for row in rows:
            db.session.delete(row)
        # Packages that also have another group just lose this one (the orphan guard above refused
        # packages for which it was the only group)
        db.session.execute(DataPackageGroup.__table__.delete().where(DataPackageGroup.group_id == group.id))
        db.session.delete(group)
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to delete group {name}: {e}")
        logger.debug(traceback.format_exc())
        return _error(gettext("Failed to delete %(group_name)s: %(e)s", group_name=name, e=str(e)), 500)

    for user, directions in former.values():
        if user is not None:
            group_policy.unbind_euds(user, name, directions)
    _audit("group_deleted", f"Group: {name} · Members: {len(former)}")
    return jsonify({"success": True})


# --------------------------------------------------------------------------------------------
# Who Can See Whom
# --------------------------------------------------------------------------------------------


def _enabled_rows_clause():
    return or_(GroupUser.enabled.is_(None), GroupUser.enabled.is_(True))


@group_api.route("/api/users/visibility", methods=["GET"])
@admin_view("groups")
def get_user_visibility():
    if _ldap_enabled():
        return _ldap_refusal()
    User = _user_model()
    rows = db.session.execute(
        select(GroupUser.group_id, GroupUser.direction, User.username, Group.name)
        .join(User, User.id == GroupUser.user_id)
        .join(Group, Group.id == GroupUser.group_id)
        .where(_enabled_rows_clause())
    ).all()

    senders, receivers, names = {}, {}, {}
    for group_id, direction, username, group_name in rows:
        names[group_id] = group_name
        if direction == Group.IN:
            senders.setdefault(group_id, set()).add(username)
        elif direction == Group.OUT:
            receivers.setdefault(group_id, set()).add(username)

    # (sender, receiver) -> names of the groups carrying it
    reach = {}
    for group_id, group_senders in senders.items():
        for a in group_senders:
            for b in receivers.get(group_id, ()):
                if a != b:
                    reach.setdefault((a, b), set()).add(names[group_id])

    def named(groups):
        return sorted(g for g in groups if not g.startswith(PAIRWISE_PREFIX))

    def manual(*group_sets):
        return any(g.startswith(PAIRWISE_PREFIX) for groups in group_sets for g in groups)

    edges, seen = [], set()
    for a, b in sorted(reach):
        pair = tuple(sorted((a, b)))
        if pair in seen:
            continue
        seen.add(pair)
        forward = reach.get((a, b), set())
        backward = reach.get((b, a), set())
        if forward and backward:
            edges.append(
                {
                    "source": a,
                    "target": b,
                    "type": "solid",
                    "manual": manual(forward, backward),
                    "source_groups": named(forward),
                    "target_groups": named(backward),
                }
            )
        else:
            source, target, groups = (a, b, forward) if forward else (b, a, backward)
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "type": "dotted",
                    "manual": manual(groups),
                    "source_groups": named(groups),
                    "target_groups": [],
                }
            )
    return jsonify(edges)


def _set_membership(user, group, direction, present: bool):
    row = (
        db.session.execute(select(GroupUser).filter_by(user_id=user.id, group_id=group.id, direction=direction))
        .scalars()
        .first()
    )
    if present and row is None:
        membership = GroupUser()
        membership.user_id = user.id
        membership.group_id = group.id
        membership.direction = direction
        db.session.add(membership)
    elif not present and row is not None:
        db.session.delete(row)


@group_api.route("/api/users/visibility", methods=["PUT"])
@roles_required("administrator")
def set_user_visibility():
    if _ldap_enabled():
        return _ldap_refusal()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    source_name = bleach.clean(str(data.get("source"))) if data.get("source") else None
    target_name = bleach.clean(str(data.get("target"))) if data.get("target") else None
    kind = data.get("type")
    if not source_name or not target_name or kind not in ("solid", "dotted"):
        return _error(gettext("source, target, and a valid type (solid or dotted) are required"))
    if source_name == target_name:
        return _error(gettext("A user can't connect to themselves"))
    source = _find_user(source_name)
    target = _find_user(target_name)
    if source is None or target is None:
        return _error(gettext("User not found"), 404)
    if source.id == target.id:
        return _error(gettext("A user can't connect to themselves"))

    name = pairwise_group_name(source.username, target.username)
    group = db.session.execute(select(Group).filter_by(name=name)).scalars().first()
    if group is None:
        group = Group()
        group.name = name
        group.type = Group.SYSTEM
        group.description = pairwise_group_description(source.username, target.username)
        group.created = datetime.datetime.now(datetime.timezone.utc)
        db.session.add(group)
        db.session.flush()

    solid = kind == "solid"
    _set_membership(source, group, Group.IN, True)
    _set_membership(target, group, Group.OUT, True)
    _set_membership(target, group, Group.IN, solid)
    _set_membership(source, group, Group.OUT, solid)
    db.session.commit()

    overridden_by = []
    if not solid:
        target_in = select(GroupUser.group_id).where(
            GroupUser.user_id == target.id, GroupUser.direction == Group.IN, _enabled_rows_clause()
        )
        source_out = select(GroupUser.group_id).where(
            GroupUser.user_id == source.id, GroupUser.direction == Group.OUT, _enabled_rows_clause()
        )
        overridden_by = sorted(
            db.session.execute(
                select(Group.name).where(Group.id.in_(target_in), Group.id.in_(source_out), _not_pairwise())
            ).scalars()
        )
    return jsonify({"success": True, "overridden_by": overridden_by})


@group_api.route("/api/users/visibility", methods=["DELETE"])
@roles_required("administrator")
def remove_user_visibility():
    if _ldap_enabled():
        return _ldap_refusal()
    source = request.args.get("source")
    target = request.args.get("target")
    if not source or not target:
        return _error(gettext("source and target are required"))
    source, target = bleach.clean(source), bleach.clean(target)
    group = db.session.execute(select(Group).filter_by(name=pairwise_group_name(source, target))).scalars().first()
    if group is None:
        return _error(
            gettext(
                "This connection comes from a shared group's Access list, not a direct connection -- remove "
                "one of them from that group instead"
            )
        )
    refusal = _orphan_refusal(group)
    if refusal:
        return refusal
    for row in db.session.execute(select(GroupUser).filter_by(group_id=group.id)).scalars().all():
        db.session.delete(row)
    db.session.execute(DataPackageGroup.__table__.delete().where(DataPackageGroup.group_id == group.id))
    db.session.delete(group)
    db.session.commit()
    return jsonify({"success": True})

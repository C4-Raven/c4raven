"""
App-level account lockout: RAVEN_LOGIN_LOCKOUT_ATTEMPTS failed sign-ins for one username within
RAVEN_LOGIN_LOCKOUT_WINDOW_MINUTES lock that username for RAVEN_LOGIN_LOCKOUT_MINUTES.

Failed attempts are not counted in memory: every one of them is already a row in the audit
trail (raven/audit.py: web_login_failed, ldap_login_failed, tfa_failed, oauth_token_failed,
video_login_failed), so the count is a query over that table. That makes it the same for every
process and worker that serves logins, survives restarts, and needs no new table. Reaching the
limit writes an "account_locked" audit row; the lock lasts RAVEN_LOGIN_LOCKOUT_MINUTES from that
row. A successful sign-in (web_login, ldap_login, oauth_token) starts the count again, and
attempts refused while locked don't count towards the next lock.

Usernames are compared case-insensitively, whether or not the account exists (so a lockout
doesn't tell anyone which usernames are real).

Covered: web login (/api/login), 2FA code validation (/api/tf-validate), LDAP login
(/api/ldap_login), /oauth/token (TAK client password login) and RTSP/RTMP credentials checked by
/api/external_auth (MediaMTX).
"""

import datetime

from flask import g, jsonify, request, session
from sqlalchemy import func, select

from raven.audit import record_audit_event
from raven.extensions import db, logger

# video_login_failed (RTSP/RTMP credentials checked by MediaMTX) is audited but not counted: a
# camera or phone left with an old password retries on its own, and would lock its owner out of
# the web UI and ATAK sign-in over and over
FAILURE_ACTIONS = (
    "web_login_failed",
    "ldap_login_failed",
    "tfa_failed",
    "oauth_token_failed",
)
SUCCESS_ACTIONS = ("web_login", "ldap_login", "oauth_token")
LOCK_ACTION = "account_locked"
LOCKED_MESSAGE = "Too many failed sign-in attempts. Try again in {minutes} minutes."


def _config(app):
    try:
        attempts = int(app.config.get("RAVEN_LOGIN_LOCKOUT_ATTEMPTS", 10) or 0)
        window = float(app.config.get("RAVEN_LOGIN_LOCKOUT_WINDOW_MINUTES", 15) or 15)
        lock = float(app.config.get("RAVEN_LOGIN_LOCKOUT_MINUTES", 15) or 15)
    except (TypeError, ValueError):
        attempts, window, lock = 10, 15.0, 15.0
    return attempts, datetime.timedelta(minutes=window), datetime.timedelta(minutes=lock)


def _now():
    # The audit trail stores naive UTC
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _latest(username: str, actions, since=None):
    from raven.models.AuditEvent import AuditEvent

    query = select(func.max(AuditEvent.timestamp)).where(
        AuditEvent.action.in_(actions),
        func.lower(AuditEvent.username) == username.lower(),
    )
    if since is not None:
        query = query.where(AuditEvent.timestamp >= since)
    return db.session.execute(query).scalar()


def locked_until(username) -> datetime.datetime | None:
    """When the lock on this username ends (naive UTC), or None if it isn't locked."""
    from flask import current_app as app

    attempts, _window, lock = _config(app)
    if attempts <= 0 or not username:
        return None
    try:
        locked_at = _latest(str(username), (LOCK_ACTION,), _now() - lock)
    except BaseException as e:
        logger.error(f"Lockout check failed for {username}: {e}")
        return None
    return locked_at + lock if locked_at else None


def is_locked(username) -> bool:
    return locked_until(username) is not None


def locked_message() -> str:
    from flask import current_app as app

    _attempts, _window, lock = _config(app)
    return LOCKED_MESSAGE.format(minutes=int(lock.total_seconds() // 60))


def register_failure(username, pending_action: str | None = None) -> bool:
    """Call after a failed sign-in for `username`. Locks the username if this failure reaches
    the limit. Returns True if it is (now) locked. Never raises.

    :param pending_action: the audit action of this failure, when its audit row may not have
        been written yet (the failed-login hook in raven/audit.py runs in after_request too);
        it's counted once more unless this request already recorded it.
    """
    from flask import current_app as app
    from raven.models.AuditEvent import AuditEvent

    attempts, window, lock = _config(app)
    if attempts <= 0 or not username:
        return False
    username = str(username)
    try:
        if is_locked(username):
            return True
        now = _now()
        since = now - window
        last_success = _latest(username, SUCCESS_ACTIONS, since)
        if last_success and last_success > since:
            since = last_success
        # Attempts refused during an earlier lock don't count towards the next one
        last_lock = _latest(username, (LOCK_ACTION,), since - lock)
        if last_lock and last_lock + lock > since:
            since = last_lock + lock

        count = db.session.execute(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action.in_(FAILURE_ACTIONS),
                func.lower(AuditEvent.username) == username.lower(),
                AuditEvent.timestamp > since,
            )
        ).scalar() or 0
        if pending_action and f"auth/{pending_action}" not in (g.get("_audit_recorded") or []):
            count += 1

        if count < attempts:
            return False

        record_audit_event(
            "auth",
            LOCK_ACTION,
            username=username,
            success=False,
            details=(
                f"{count} failed sign-in attempts within {int(window.total_seconds() // 60)} "
                f"minutes: locked for {int(lock.total_seconds() // 60)} minutes"
            ),
        )
        logger.warning(f"Locked {username} after {count} failed sign-in attempts")
        return True
    except BaseException as e:
        logger.error(f"Failed to update the lockout count for {username}: {e}")
        return False


def _locked_response():
    response = jsonify({"meta": {"code": 429}, "response": {"errors": [locked_message()]}})
    response.status_code = 429
    return response


def _web_login_username(app, path):
    """(username, failure action) for a sign-in request to `path`, or (None, None)."""
    prefix = app.config.get("SECURITY_URL_PREFIX") or ""
    login_url = prefix + (app.config.get("SECURITY_LOGIN_URL") or "/login")
    tf_url = prefix + (app.config.get("SECURITY_TWO_FACTOR_TOKEN_VALIDATION_URL") or "/tf-validate")
    path = path.rstrip("/") or "/"

    if path in (login_url, "/api/ldap_login"):
        action = "web_login_failed" if path == login_url else "ldap_login_failed"
        try:
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                data = request.form
            for field in ("username", "email", "identity"):
                if data.get(field):
                    return str(data.get(field)), action
        except BaseException:
            pass
        return None, action
    if path == tf_url:
        uniquifier = session.get("tf_user_id")
        if uniquifier:
            user = app.security.datastore.find_user(fs_uniquifier=uniquifier)
            if user:
                return user.username, "tfa_failed"
        return None, "tfa_failed"
    return None, None


def init_lockout(app):
    """Refuse web sign-ins for a locked username, and count failed ones."""

    @app.before_request
    def _refuse_locked_login():
        if request.method != "POST":
            return None
        username, action = _web_login_username(app, request.path)
        if action and username and is_locked(username):
            g._lockout_refused = True
            logger.warning(f"Refused sign-in for locked account {username}")
            return _locked_response()
        return None

    @app.after_request
    def _count_failed_login(response):
        try:
            if request.method != "POST" or response.status_code < 400:
                return response
            if g.get("_lockout_refused"):
                return response
            username, action = _web_login_username(app, request.path)
            if action and username:
                register_failure(username, pending_action=action)
        except BaseException as e:
            logger.error(f"Lockout hook error: {e}")
        return response

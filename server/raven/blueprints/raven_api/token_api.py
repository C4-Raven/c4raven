"""
Bearer tokens for TAKX/CloudTAK (/oauth/token) and per-user ATAK enrollment QR tokens.

Each user has at most one stored enrollment Token. Its QR string embeds a signed JWT that ATAK
presents as the password when it enrolls for a certificate; Token.verify_token finds the row by
the hash of the JWT's claims.
"""

import datetime
import os
import time
import traceback
from urllib.parse import urlparse

import bleach
import jwt
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from flask_security import auth_required, current_user
from flask_security.utils import verify_password

from raven.audit import record_audit_event
from raven.extensions import db, logger
from raven.login_lockout import is_locked, locked_message, register_failure

token_api_blueprint = Blueprint("token_api_blueprint", __name__)

OAUTH_TOKEN_LIFETIME = datetime.timedelta(days=365)
# Unix seconds past this (2038-01-19) are taken to be milliseconds
_MAX_SECONDS = 2147483647
_INVALID_CREDENTIALS = "Invalid username or password"


def _signing_key():
    path = os.path.join(app.config.get("RAVEN_CA_FOLDER"), "certs", "raven", "raven.nopass.key")
    with open(path, "rb") as f:
        return f.read()


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


# ------------------------------------------------------------------------------- /oauth/token


def _credential(name):
    value = request.args.get(name)
    if value is None:
        value = request.form.get(name)
    return bleach.clean(value) if value is not None else ""


def _authenticate(username, password):
    """The user these credentials belong to, or None."""
    if app.config.get("RAVEN_ENABLE_LDAP"):
        from flask_ldap3_login import AuthenticationResponseStatus

        from raven.blueprints.raven_api.ldap_api import save_user
        from raven.extensions import ldap_manager

        result = ldap_manager.authenticate(username, password)
        if result.status != AuthenticationResponseStatus.success:
            return None
        return save_user(result.user_dn, username, result.user_info, result.user_groups)

    user = app.security.datastore.find_user(username=username)
    if user and user.password and verify_password(password, user.password):
        return user
    return None


@token_api_blueprint.route("/oauth/token", methods=["GET", "POST"])
def cloudtak_oauth_token():
    username = _credential("username")
    password = _credential("password")
    if not username or not password:
        return _error(_INVALID_CREDENTIALS, 400)

    if is_locked(username):
        return _error(locked_message(), 429)

    user = _authenticate(username, password)
    if user is None:
        record_audit_event("auth", "oauth_token_failed", username=username, success=False)
        register_failure(username)
        return _error(_INVALID_CREDENTIALS, 400)

    if not user.active:
        record_audit_event(
            "auth", "oauth_token_refused", username=user.username, success=False, details="Account is deactivated"
        )
        return _error(_INVALID_CREDENTIALS, 400)

    if getattr(user, "force_password_change", False):
        record_audit_event(
            "auth",
            "oauth_token_refused",
            username=user.username,
            success=False,
            details="Password change required before a token can be issued",
        )
        return _error("You must change your password on the web page before signing in here", 403)

    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "exp": now + OAUTH_TOKEN_LIFETIME,
        "nbf": now,
        "iss": "Raven",
        "aud": "Raven",
        "iat": now,
        "sub": user.username,
    }
    access_token = jwt.encode(claims, _signing_key(), algorithm="RS256")
    record_audit_event("auth", "oauth_token", username=user.username, details="TAKX/CloudTAK OAuth token issued")
    return jsonify(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": int(OAUTH_TOKEN_LIFETIME.total_seconds()),
        }
    )


# ---------------------------------------------------------------------- ATAK enrollment QR


def _is_admin():
    return current_user.has_role("administrator")


def _target_username():
    """GET/DELETE: an administrator may name another user; everyone else gets their own."""
    requested = request.args.get("username")
    if requested and _is_admin():
        return requested
    return current_user.username


def _unix_seconds(value):
    """Body exp/nbf: unix seconds or milliseconds, or None to leave the stored value alone."""
    if not value:
        return None
    value = int(value)
    if value > _MAX_SECONDS:
        value = value // 1000
    return value


def _max_uses(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _qr_string(token):
    host = urlparse(request.url_root).hostname
    return f"tak://com.atakmap.app/enroll?host={host}&username={token.username}&token={token.generate_token()}"


def _token_json(token, **extra):
    data = token.to_json()
    data.update(success=True, disabled=token.disabled, qr_string=_qr_string(token), **extra)
    return data


def _describe(token):
    uses = token.max_uses if token.max_uses else "unlimited"
    if token.expiration:
        expires = datetime.datetime.fromtimestamp(token.expiration, tz=datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    else:
        expires = "never"
    details = f"ATAK enrollment QR token: max uses {uses}, expires {expires}"
    if token.disabled:
        details += ", disabled"
    return details


@token_api_blueprint.route("/api/atak_qr_string", methods=["POST"])
@auth_required()
def new_atak_qr_string():
    from raven.models.Token import Token

    try:
        body = request.get_json(silent=True) or {}
        username = body.get("username") or current_user.username

        if username != current_user.username:
            # A name the datastore lookup resolves to the caller's own account is still the caller
            user = app.security.datastore.find_user(username=username)
            if user is None or user.id != current_user.id:
                if not _is_admin():
                    return _error("Cannot generate QR for another user", 401)
                if user is None:
                    return _error(f"No such user: {username}", 404)
            username = user.username

        token = db.session.query(Token).filter(Token.username == username).first()
        if token is None:
            token = Token()
            token.username = username
            token.creation = int(time.time())

        expiration = _unix_seconds(body.get("exp"))
        if expiration is not None:
            token.expiration = expiration
        not_before = _unix_seconds(body.get("nbf"))
        if not_before is not None:
            token.not_before = not_before
        max_uses = _max_uses(body.get("max"))
        if max_uses is not None:
            token.max_uses = max_uses
        token.disabled = body["disabled"] if "disabled" in body else None

        token.hash_token()
        db.session.add(token)
        db.session.commit()

        record_audit_event(
            "enrollment", "qr_generated", username=username, actor=current_user.username, details=_describe(token)
        )
        return jsonify(_token_json(token))
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to create an ATAK QR token: {e}")
        logger.debug(traceback.format_exc())
        return _error(str(e), 400)


@token_api_blueprint.route("/api/atak_qr_string", methods=["GET"])
@auth_required()
def get_atak_qr_strings():
    from raven.models.Token import Token

    username = _target_username()
    token = db.session.query(Token).filter(Token.username == username).first()
    if token is None:
        return _error(gettext("No token found for %(username)s", username=username), 404)
    return jsonify(_token_json(token, total_uses=int(token.total_uses or 0)))


@token_api_blueprint.route("/api/atak_qr_string", methods=["DELETE"])
@auth_required()
def delete_token():
    from raven.models.Token import Token

    username = _target_username()
    try:
        db.session.query(Token).filter(Token.username == username).delete(synchronize_session=False)
        db.session.commit()
    except BaseException as e:
        db.session.rollback()
        logger.error(f"Failed to delete the ATAK QR token of {username}: {e}")
        logger.debug(traceback.format_exc())
        return _error(gettext("Failed to delete token: %(e)s", e=str(e)), 500)

    record_audit_event("enrollment", "qr_token_deleted", username=username, actor=current_user.username)
    return jsonify({"success": True})

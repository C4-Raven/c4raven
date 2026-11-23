"""
Who is calling a Marti route: a TAK client's client certificate (forwarded by nginx from the
8443 port, which requires one) or a logged-in web session.

nginx clears the certificate header on every port that doesn't verify a client cert (the
public web ports, and 8446 except /oauth), so a request there carries no identity unless it
has a web session. Routes that hand out device positions, contacts or CoT must not answer such
requests (see marti_caller_required).

Deactivated accounts are refused here too: their certificates stay valid until revoked, but
the account being switched off has to stop them reading data the same way it stops a login.
"""

from functools import wraps

from flask import current_app as app
from flask import jsonify

from raven.extensions import logger


def _cert_user(cert):
    """(the user named by a verified client cert or None, whether the cert is acceptable)."""
    try:
        username = cert.get_subject().commonName
    except BaseException:
        return None, False
    user = app.security.datastore.find_user(username=username) if username else None
    if user is not None and not user.active:
        return user, False
    return user, True


def marti_caller():
    """The identity behind this Marti request.

    :returns: (ok, user). ok is False for an anonymous caller, an invalid/revoked cert or a
        deactivated user. user is the account (None for a valid cert whose CN isn't a user,
        e.g. a server or service cert issued by this CA).
    """
    from flask_login import current_user

    try:
        if current_user and current_user.is_authenticated:
            return bool(getattr(current_user, "active", True)), current_user
    except BaseException:
        pass

    # Looked up through the module so tests (and any later hook) can replace it in one place.
    # import_module, not "from raven.blueprints.marti_api import marti_api": the package
    # re-exports the Blueprint objects under their module names.
    import importlib

    marti_api_module = importlib.import_module("raven.blueprints.marti_api.marti_api")

    try:
        cert = marti_api_module.verify_client_cert()
    except BaseException as e:
        # A malformed header is the same as no certificate
        logger.debug(f"Unreadable client certificate: {e}")
        return False, None
    if not cert:
        return False, None
    user, ok = _cert_user(cert)
    return ok, user


def marti_user():
    """The active user behind this Marti request (cert or web session), or None."""
    ok, user = marti_caller()
    return user if ok else None


def marti_caller_required(view):
    """Answer 401 unless the request has a valid client certificate of an active (or unknown,
    CA-issued) account, or an authenticated web session."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        ok, _user = marti_caller()
        if not ok:
            return jsonify({"success": False, "error": "Client certificate required"}), 401
        return view(*args, **kwargs)

    return wrapper

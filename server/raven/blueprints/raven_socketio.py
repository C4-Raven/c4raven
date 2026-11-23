"""Socket.IO connection handlers for the web UI and the auth decorators other handlers use.

The web UI connects to the "/socket.io" namespace. Only signed-in users may stay connected;
admin-only events are wrapped with ``administrator_only``.
"""

import functools

from flask import Blueprint, request
from flask_security import current_user
from flask_socketio import disconnect

from raven.extensions import logger, socketio

NAMESPACE = "/socket.io"

# No routes: registering the blueprint makes sure this module (and its handlers) is imported.
raven_socketio_blueprint = Blueprint("raven_socketio_blueprint", __name__)


def _drop_client():
    # Looked up through module globals at call time so tests can substitute them.
    disconnect(request.sid, namespace=request.namespace)


def authenticated_only(f):
    """Disconnect the client instead of running ``f`` unless the user is signed in."""

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            _drop_client()
            return None
        return f(*args, **kwargs)

    return wrapper


def administrator_only(f):
    """Disconnect the client instead of running ``f`` unless the user is a signed-in administrator."""

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.has_role("administrator"):
            _drop_client()
            return None
        return f(*args, **kwargs)

    return wrapper


@socketio.on("connect", namespace=NAMESPACE)
@authenticated_only
def connect(auth=None):
    logger.debug("Socket.IO client connected: {}".format(current_user.username))


@socketio.on("message", namespace=NAMESPACE)
@authenticated_only
def message(data=None):
    logger.debug("Socket.IO message received: {}".format(data))

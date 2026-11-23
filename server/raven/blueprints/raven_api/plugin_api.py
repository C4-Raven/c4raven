"""Server-side (Python) plugins: list, inspect, enable/disable, upload a plugin archive and report
the configured plugin repository. Works on the app's ``plugin_manager``, which only exists when
plugins are enabled."""

import os
import traceback
import urllib.parse

from flask import Blueprint, current_app as app, jsonify, request
from flask_babel import gettext
from flask_security import current_user, roles_required
from werkzeug.utils import secure_filename

from raven.admin_view import admin_view, is_admin_viewer
from raven.extensions import logger
from raven.plugins import plugin_repo

plugin_blueprint = Blueprint("plugin_api_blueprint", __name__)

ALLOWED_PLUGIN_EXTENSIONS = ("zip", "whl", "gz")


def mask_url_userinfo(url: str) -> str:
    """The URL with any user:password@ replaced by ***@, for read-only admins. A private index URL
    often carries its credentials."""
    if not url or "@" not in url:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "***"
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urllib.parse.urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))


def _plugin_manager():
    return getattr(app, "plugin_manager", None)


def _plugins_disabled():
    return jsonify({"success": False, "error": gettext("Plugins are disabled")}), 400


@plugin_blueprint.route("/api/plugins", methods=["GET"])
@admin_view("plugins")
def get_plugins():
    manager = _plugin_manager()
    if not manager:
        return jsonify({"success": True, "plugins": []})
    plugins = [plugin.get_info() for plugin in manager.plugins.values()]
    return jsonify({"success": True, "plugins": plugins})


@plugin_blueprint.route("/api/plugins", methods=["POST"])
@roles_required("administrator")
def upload_plugin():
    if not _plugin_manager():
        return _plugins_disabled()
    if not request.files:
        return jsonify({"error": "You must upload a plugin file"}), 400
    try:
        upload = next(iter(request.files.values()))
        filename = secure_filename(upload.filename or "")
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not filename or extension not in ALLOWED_PLUGIN_EXTENSIONS:
            return (
                jsonify({"success": False, "error": gettext("Server plugins must be zip, whl, or tar.gz files")}),
                400,
            )
        folder = app.config.get("UPLOAD_FOLDER")
        os.makedirs(folder, exist_ok=True)
        upload.save(os.path.join(folder, filename))
        return jsonify({"success": True})
    except BaseException as e:
        logger.error(f"Failed to save the uploaded plugin: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500


@plugin_blueprint.route("/api/plugins/repo", methods=["GET"])
@admin_view("plugins")
def get_plugin_repo():
    repo_url = plugin_repo.get_plugin_repo(app.config)
    # No login manager means no session user at all (the view called directly, as in tests)
    if getattr(app, "login_manager", None) is not None and is_admin_viewer(current_user):
        repo_url = mask_url_userinfo(repo_url)
    return jsonify({"success": True, "repo_url": repo_url})


@plugin_blueprint.route("/api/plugins/<plugin_name>", methods=["GET"])
@plugin_blueprint.route("/api/plugins/<plugin_name>/", methods=["GET"])
@admin_view("plugins")
def get_plugin(plugin_name):
    manager = _plugin_manager()
    if not manager:
        return _plugins_disabled()
    plugin = manager.plugins.get(plugin_name)
    if plugin is None:
        return (
            jsonify({"success": False, "error": gettext("Plugin %(plugin_name)s not found", plugin_name=plugin_name)}),
            404,
        )
    metadata = dict(plugin.load_metadata() or {})
    metadata["enabled"] = bool(manager.check_if_plugin_enabled(plugin_name.lower()))
    return jsonify(metadata)


def _toggle(plugin_name, enable: bool):
    manager = _plugin_manager()
    if not manager:
        return _plugins_disabled()
    try:
        if enable:
            manager.enable_plugin(plugin_name)
        else:
            manager.disable_plugin(plugin_name)
        return jsonify({"success": True})
    except BaseException as e:
        logger.error(f"Failed to {'enable' if enable else 'disable'} plugin {plugin_name}: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"success": False, "error": str(e)})


@plugin_blueprint.route("/api/plugins/<plugin_name>/disable", methods=["POST"])
@roles_required("administrator")
def disable_plugin(plugin_name):
    return _toggle(plugin_name, False)


@plugin_blueprint.route("/api/plugins/<plugin_name>/enable", methods=["POST"])
@roles_required("administrator")
def enable_plugin(plugin_name):
    return _toggle(plugin_name, True)

"""Discovery, activation and package management of Raven server plugins.

Plugins are pip distributions exposing a ``Plugin`` subclass in the ``raven.plugin`` entry-point group.
Administrators install and remove them at runtime through the ``plugin_package_manager`` Socket.IO
event; pip's output is streamed back to the requesting browser.
"""

import os
import queue
import re
import subprocess
import sys
import threading
from importlib import metadata

from flask import Flask, current_app, request
from werkzeug.utils import secure_filename

from raven.blueprints.raven_socketio import administrator_only
from raven.extensions import db, logger, socketio
from raven.plugins.Plugin import Plugin
from raven.plugins.plugin_repo import NO_PLUGIN_REPO_MESSAGE, get_plugin_repo

NAMESPACE = "/socket.io"
EVENT = "plugin_package_manager"

# A pip distribution name, optionally pinned to one version. Nothing else may reach the pip command line.
PLUGIN_DISTRO_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.\-]*(?:==[A-Za-z0-9_.+!\-]+)?"
_PLUGIN_DISTRO_RE = re.compile(PLUGIN_DISTRO_PATTERN)

LOCAL_PLUGIN_SUFFIXES = (".whl", ".zip", ".tar.gz")


def _plugins_model():
    from raven.models.Plugins import Plugins

    return Plugins


def _reply(sid, payload, ignore_queue=True):
    socketio.emit(EVENT, payload, namespace=NAMESPACE, to=sid, ignore_queue=ignore_queue)


def _valid_distro(plugin_distro, prefixes) -> bool:
    if not isinstance(plugin_distro, str) or not _PLUGIN_DISTRO_RE.fullmatch(plugin_distro):
        return False
    return any(plugin_distro.startswith(prefix) for prefix in prefixes or [])


def _bare_name(plugin_distro: str) -> str:
    """The distribution name without any ``==version`` pin, lower-cased."""
    return plugin_distro.split("==", 1)[0].lower()


def _pump(stream, out: queue.Queue):
    try:
        while True:
            chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
            if not chunk:
                break
            out.put(chunk)
    except Exception as e:  # noqa: BLE001
        logger.debug("Plugin command output reader stopped: {}".format(e))
    finally:
        out.put(None)


class PluginManager:
    def __init__(self, group: str, app: Flask):
        self.group = group
        self.app = app
        self.plugins: dict[str, Plugin] = {}

    def get_plugin_entry_points(self) -> list:
        return list(metadata.entry_points(group=self.group))

    def load_plugins(self):
        for entry_point in self.get_plugin_entry_points():
            plugin_class = entry_point.load()
            if not isinstance(plugin_class, type) or not issubclass(plugin_class, Plugin):
                raise ValueError("The Raven plugin must be an instance of Plugin")
            try:
                plugin = plugin_class()
                plugin.load_metadata()
                self.plugins[plugin.distro.lower()] = plugin
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to load plugin {}: {}".format(entry_point.name, e))

    def activate(self, *args, **kwargs):
        app = args[0] if args else kwargs.get("app", self.app)
        Plugins = _plugins_model()
        for plugin in list(self.plugins.values()):
            try:
                with app.app_context():
                    plugin_metadata = plugin.load_metadata() or {}
                    try:
                        row = db.session.execute(
                            db.select(Plugins).filter_by(name=plugin.name.lower())
                        ).scalar_one_or_none()
                        if row is not None:
                            row.version = plugin_metadata.get("version")
                        else:
                            db.session.add(
                                Plugins(
                                    name=plugin.name.lower(),
                                    distro=plugin_metadata.get("distro"),
                                    author=plugin_metadata.get("author"),
                                    version=plugin_metadata.get("version"),
                                    enabled=True,
                                )
                            )
                        db.session.commit()
                    except Exception as e:  # noqa: BLE001
                        db.session.rollback()
                        logger.debug("Could not record plugin {}: {}".format(plugin.name, e))

                    plugin.activate(app, enabled=self.check_if_plugin_enabled(plugin.name))
                if plugin.blueprint is not None:
                    app.register_blueprint(plugin.blueprint)
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to activate plugin {}: {}".format(getattr(plugin, "name", plugin), e))

    def stop_plugins(self):
        for plugin in self.plugins.values():
            plugin.stop()

    def _set_enabled(self, plugin: Plugin, enabled: bool):
        Plugins = _plugins_model()
        row = db.session.execute(db.select(Plugins).filter_by(name=plugin.name.lower())).scalar_one_or_none()
        if row is not None:
            row.enabled = enabled
        db.session.commit()

    def disable_plugin(self, plugin_distro: str):
        plugin = self.plugins[plugin_distro]
        plugin.stop()
        self._set_enabled(plugin, False)

    def enable_plugin(self, plugin_distro: str):
        plugin = self.plugins[plugin_distro]
        plugin.activate(self.app, True)
        self._set_enabled(plugin, True)

    def check_if_plugin_enabled(self, plugin_distro: str) -> bool:
        Plugins = _plugins_model()
        row = db.session.execute(db.select(Plugins).filter_by(name=plugin_distro.lower())).scalar_one_or_none()
        if row is None:
            return True
        return bool(row.enabled)

    def _register_installed(self, plugin_distro: str):
        """After a by-name install: find the new plugin, record it and switch it on."""
        wanted = _bare_name(plugin_distro)
        Plugins = _plugins_model()
        for entry_point in self.get_plugin_entry_points():
            plugin_class = entry_point.load()
            if not isinstance(plugin_class, type) or not issubclass(plugin_class, Plugin):
                continue
            plugin = plugin_class()
            plugin.load_metadata()
            if plugin.distro.lower() != wanted:
                continue
            self.plugins[wanted] = plugin
            db.session.add(
                Plugins(
                    name=plugin.name.lower(),
                    distro=plugin.distro,
                    author=plugin.metadata.get("author"),
                    version=plugin.metadata.get("version"),
                    enabled=True,
                )
            )
            db.session.commit()
            break
        self.enable_plugin(wanted)

    def _unregister_deleted(self, plugin_distro: str):
        name = _bare_name(plugin_distro)
        self.plugins.pop(name, None)
        Plugins = _plugins_model()
        db.session.execute(db.delete(Plugins).where(db.func.lower(Plugins.distro) == name))
        db.session.commit()

    @staticmethod
    @socketio.on(EVENT, namespace=NAMESPACE)
    @administrator_only
    def install_plugin(json: dict):
        config = current_app.config
        sid = request.sid
        json = json if isinstance(json, dict) else {}
        action = json.get("action")
        plugin_distro = json.get("plugin_distro")
        upload_folder = config.get("UPLOAD_FOLDER")
        local_file = None

        if action != "install_local" and not _valid_distro(plugin_distro, config.get("RAVEN_PLUGIN_PREFIXES")):
            _reply(sid, {"success": False, "message": "Invalid Plugin: {}".format(plugin_distro)}, ignore_queue=False)
            return

        python = sys.executable
        if action == "delete":
            try:
                current_app.plugin_manager.disable_plugin(_bare_name(plugin_distro))
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not disable plugin {} before removal: {}".format(plugin_distro, e))
            command = "{} -m pip uninstall --yes {}".format(python, plugin_distro)
        elif action == "install":
            repo = get_plugin_repo(config)
            if not repo:
                _reply(sid, {"success": False, "message": NO_PLUGIN_REPO_MESSAGE})
                return
            command = "{} -m pip --no-input install {} -i {}".format(python, plugin_distro, repo)
        elif action == "install_local":
            file_name = secure_filename(json.get("plugin_file_name") or "")
            path = os.path.join(upload_folder or "", file_name)
            if not file_name or not file_name.endswith(LOCAL_PLUGIN_SUFFIXES) or not os.path.isfile(path):
                _reply(sid, {"success": False, "message": "Plugin file not found: {}".format(file_name)})
                return
            local_file = path
            command = "{} -m pip --no-input install {}".format(python, path)
        else:
            _reply(sid, {"success": False, "message": "Invalid action: {}".format(action)})
            return

        _reply(sid, {"message": "$ {}\n".format(command)})

        try:
            process = subprocess.Popen(
                command.split(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except Exception as e:  # noqa: BLE001
            _reply(sid, {"success": False, "message": "Command failed: {}".format(e)})
            return

        try:
            if process.stdin:
                process.stdin.close()
            output: queue.Queue = queue.Queue()
            readers = [
                threading.Thread(target=_pump, args=(stream, output), daemon=True)
                for stream in (process.stdout, process.stderr)
                if stream is not None
            ]
            for reader in readers:
                reader.start()
            open_streams = len(readers)
            while open_streams:
                chunk = output.get()
                if chunk is None:
                    open_streams -= 1
                    continue
                _reply(sid, {"message": chunk.decode("utf-8", errors="replace")})
            return_code = process.wait()

            if return_code == 0:
                _reply(sid, {"success": True, "message": "Command completed successfully"})
                manager = current_app.plugin_manager
                if action == "install":
                    manager._register_installed(plugin_distro)
                elif action == "delete":
                    manager._unregister_deleted(plugin_distro)
                else:
                    manager.load_plugins()
            else:
                _reply(sid, {"success": False, "message": "Command failed with return code: {}".format(return_code)})
        finally:
            if local_file:
                try:
                    os.remove(local_file)
                except OSError as e:
                    logger.warning("Could not delete uploaded plugin file {}: {}".format(local_file, e))

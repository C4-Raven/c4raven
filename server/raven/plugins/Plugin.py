from abc import abstractmethod

from flask import Flask, current_app, url_for

from raven.plugins.BasePlugin import BasePlugin


class Plugin(BasePlugin):
    """Base class for Raven server plugins.

    A plugin is a pip-installable distribution exposing a subclass of this class through the
    ``raven.plugin`` entry-point group. Set ``blueprint`` to a Flask Blueprint to have it registered.
    """

    group = "raven.plugin"
    blueprint = None

    def __init__(self):
        self._app = None
        self._config = {}
        self.metadata = {}
        self.name = ""
        self.distro = ""
        self.routes = []

    @abstractmethod
    def activate(self, app: Flask, enabled: bool) -> None:
        """Start the plugin. ``enabled`` says whether the administrator has it switched on."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the plugin."""

    @abstractmethod
    def get_info(self) -> dict | None:
        """Information shown for this plugin in the plugin list."""

    @abstractmethod
    def load_metadata(self) -> dict:
        """Fill ``self.name``, ``self.distro`` and ``self.metadata`` and return the metadata.

        The metadata must contain at least ``distro``, ``author`` and ``version``.
        """

    @staticmethod
    def has_no_empty_params(rule) -> bool:
        defaults = rule.defaults if rule.defaults is not None else ()
        arguments = rule.arguments if rule.arguments is not None else ()
        return len(defaults) >= len(arguments)

    def get_plugin_routes(self, url_prefix: str):
        for rule in current_app.url_map.iter_rules():
            methods = rule.methods or set()
            if "GET" not in methods and "POST" not in methods:
                continue
            if not self.has_no_empty_params(rule):
                continue
            url = url_for(rule.endpoint, **(rule.defaults or {}))
            if url.startswith(url_prefix) and url not in self.routes:
                self.routes.append(url)

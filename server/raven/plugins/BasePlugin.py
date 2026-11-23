from abc import ABC, abstractmethod


class BasePlugin(ABC):
    """Root of the plugin class hierarchy."""

    PLUGIN_API_VERSION = "1.0.0"

    @property
    @abstractmethod
    def group(self) -> str:
        """The entry-point group plugins of this kind are published under."""

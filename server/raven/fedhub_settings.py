"""Whether this server is connected to a Federation Hub.

Federation is off by default with no hub address, and nothing talks to a hub unless an
address is configured in config.yml."""

import requests


class HubNotConfigured(requests.exceptions.ConnectionError):
    """Raised instead of making a request when no Federation Hub is set up here. A ConnectionError,
    so code that copes with an unreachable hub copes with this too, without any network traffic."""


def _text(config, key) -> str:
    return str(config.get(key) or "").strip()


def hub_api_configured(config) -> bool:
    """True when the admin API of a hub may be called: federation on and an API address set."""
    return bool(config.get("RAVEN_FEDHUB_ENABLE")) and bool(_text(config, "RAVEN_FEDHUB_API_ADDRESS"))


def bridge_configured(config) -> bool:
    """True when the bridge should connect: federation and federating on, and a broker address set."""
    return (
        bool(config.get("RAVEN_FEDHUB_ENABLE"))
        and bool(config.get("RAVEN_FEDHUB_FEDERATE_ENABLE"))
        and bool(_text(config, "RAVEN_FEDHUB_BROKER_ADDRESS"))
    )

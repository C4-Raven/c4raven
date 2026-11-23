"""A fresh install points at no Federation Hub and makes no hub calls."""

import socket

import pytest
import yaml

from raven import fedhub_names
from raven.config_file import default_config_dict
from raven.defaultconfig import DefaultConfig
from raven.fedhub_settings import (
    HubNotConfigured,
    bridge_configured,
    hub_api_configured,
)

# A logged-in administrator client on the shared app fixture
from tests.test_fedhub_names import admin  # noqa: F401

FEDHUB_KEYS = (
    "RAVEN_FEDHUB_ENABLE",
    "RAVEN_FEDHUB_FEDERATE_ENABLE",
    "RAVEN_FEDHUB_API_ADDRESS",
    "RAVEN_FEDHUB_BROKER_ADDRESS",
)


def test_fresh_config_points_at_no_hub():
    conf = default_config_dict()
    assert conf["RAVEN_FEDHUB_ENABLE"] is False
    assert conf["RAVEN_FEDHUB_FEDERATE_ENABLE"] is False
    assert conf["RAVEN_FEDHUB_API_ADDRESS"] == ""
    assert conf["RAVEN_FEDHUB_BROKER_ADDRESS"] == ""
    assert ":9102" not in yaml.safe_dump(conf)
    assert not hub_api_configured(conf) and not bridge_configured(conf)


@pytest.fixture
def no_hub(app):
    saved = {k: app.config.get(k) for k in FEDHUB_KEYS}
    app.config.update(
        RAVEN_FEDHUB_ENABLE=False,
        RAVEN_FEDHUB_FEDERATE_ENABLE=False,
        RAVEN_FEDHUB_API_ADDRESS="",
        RAVEN_FEDHUB_BROKER_ADDRESS="",
    )
    fedhub_names.reset()
    yield app
    app.config.update(saved)
    fedhub_names.reset()


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a network connection was attempted")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_admin_pages_make_no_hub_call_without_a_hub(no_hub, no_network, admin):  # noqa: F811
    overview = admin.get("/api/fedhub/overview")
    assert overview.status_code == 200
    assert "not set up" in overview.json.get("error", "")
    proxied = admin.get("/api/fedhub/connections")
    assert proxied.status_code == 503
    assert proxied.json["error"] == "Federation Hub is not set up on this server"


def test_name_lookup_makes_no_hub_call_without_a_hub(no_hub, no_network):
    with no_hub.app_context():
        request = fedhub_names.request_fn(no_hub.config)
        with pytest.raises(HubNotConfigured):
            request("GET", "/x")
        assert fedhub_names.resolver()("some-provenance-id") is None

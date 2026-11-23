"""RAVEN_MAP_ENABLE: some sites must not have browsers pull map tiles from public servers, so the
installer lets them turn the web map off. The UI asks /api/features; the map API refuses when off."""

import pytest

from raven.defaultconfig import DefaultConfig

# A logged-in administrator client on the shared app fixture
from tests.test_fedhub_names import admin  # noqa: F401


@pytest.fixture
def map_setting(app):
    original = app.config.get("RAVEN_MAP_ENABLE")
    yield app.config
    app.config["RAVEN_MAP_ENABLE"] = original


def test_map_is_on_by_default():
    assert DefaultConfig.RAVEN_MAP_ENABLE is True


def test_features_reports_map_on(admin, map_setting):  # noqa: F811
    map_setting["RAVEN_MAP_ENABLE"] = True
    assert admin.get("/api/features").json["map"] is True
    assert admin.get("/api/map_state").status_code == 200


def test_map_off_hides_feature_and_refuses_map_state(admin, map_setting):  # noqa: F811
    map_setting["RAVEN_MAP_ENABLE"] = False
    assert admin.get("/api/features").json["map"] is False
    response = admin.get("/api/map_state")
    assert response.status_code == 404
    assert response.json["error"] == "The map is turned off on this server"


def test_features_needs_sign_in(app):
    response = app.test_client().get("/api/features", headers={"Accept": "application/json"})
    assert response.status_code == 401

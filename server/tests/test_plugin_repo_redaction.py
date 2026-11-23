"""GET /api/plugins/repo: read-only admins never see credentials embedded in the repository URL
(raven/blueprints/raven_api/plugin_api.py)."""

from types import SimpleNamespace

import pytest
from flask import Flask

from raven.blueprints.raven_api import plugin_api
from raven.defaultconfig import DefaultConfig

SECRET_URL = "https://deploy:S3cret@pypi.example.com/simple"


@pytest.mark.parametrize(
    "url, expected",
    [
        (SECRET_URL, "https://***@pypi.example.com/simple"),
        ("https://token@pypi.example.com:8443/simple/", "https://***@pypi.example.com:8443/simple/"),
        ("https://pypi.example.com/simple/", "https://pypi.example.com/simple/"),
        ("https://pypi.example.com/simple/?q=a@b", "https://pypi.example.com/simple/?q=a@b"),
        ("", ""),
    ],
)
def test_mask_url_userinfo(url, expected):
    assert plugin_api.mask_url_userinfo(url) == expected


def _user(*roles):
    return SimpleNamespace(is_authenticated=True, has_role=lambda role: role in roles)


@pytest.fixture
def plugin_app(monkeypatch):
    monkeypatch.delenv("RAVEN_PLUGIN_REPO", raising=False)
    app = Flask("raven-plugin-repo-test")
    app.config.from_object(DefaultConfig)
    app.config.update(TESTING=True, RAVEN_PLUGIN_REPO=SECRET_URL)
    # Stands in for Flask-Security's login manager; current_user is patched per test
    app.login_manager = SimpleNamespace()
    return app


def test_viewer_gets_the_repo_url_without_credentials(plugin_app, monkeypatch):
    view = plugin_api.get_plugin_repo.__wrapped__
    monkeypatch.setattr(plugin_api, "current_user", _user("user", "admin_viewer"))
    with plugin_app.test_request_context("/api/plugins/repo"):
        body = view().get_json()
    assert body == {"success": True, "repo_url": "https://***@pypi.example.com/simple"}
    assert "S3cret" not in str(body)


def test_full_admin_gets_the_whole_repo_url(plugin_app, monkeypatch):
    view = plugin_api.get_plugin_repo.__wrapped__
    monkeypatch.setattr(plugin_api, "current_user", _user("administrator", "admin_viewer"))
    with plugin_app.test_request_context("/api/plugins/repo"):
        assert view().get_json() == {"success": True, "repo_url": SECRET_URL}

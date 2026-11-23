"""Every state-changing /api route refuses a session-cookie request without a CSRF token
(raven/app.py check_api_csrf), not only the ones Flask-Security's @auth_required checks.

The views are replaced with a stub, so only the app-level hooks run: a request the CSRF check lets
through reaches the stub (204), one it refuses gets 400.
"""

import time

import pytest
from flask_security import hash_password

from raven.app import CSRF_CHECKED_METHODS, CSRF_EXEMPT_API_PATHS, CSRF_EXEMPT_API_PREFIXES
from raven.extensions import db

STUB_STATUS = 204


def _is_exempt(app, rule):
    if rule.rule in CSRF_EXEMPT_API_PATHS or rule.rule.startswith(CSRF_EXEMPT_API_PREFIXES):
        return True
    blueprint = rule.endpoint.rsplit(".", 1)[0] if "." in rule.endpoint else None
    return blueprint == app.config.get("SECURITY_BLUEPRINT_NAME", "security")


def _write_routes(app):
    routes = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/") or _is_exempt(app, rule):
            continue
        for method in sorted(set(rule.methods) & set(CSRF_CHECKED_METHODS)):
            values = {
                name: 1 if type(rule._converters[name]).__name__ == "IntegerConverter" else "x"
                for name in rule.arguments
                if name not in (rule.defaults or {})
            }
            with app.test_request_context():
                url = app.url_for(rule.endpoint, _method=method, **values)
            routes.append((method, url, rule.endpoint))
    return routes


@pytest.fixture
def admin_app(app):
    with app.app_context():
        ds = app.security.datastore
        ds.find_or_create_role("administrator")
        admin = ds.create_user(
            username="csrfadmin", password=hash_password("csrf-admin-pass"), roles=["administrator"]
        )
        db.session.commit()
        app.csrf_admin_uniquifier = admin.fs_uniquifier
        app.csrf_admin_token = admin.get_auth_token()

    def stub(*args, **kwargs):
        return "", STUB_STATUS

    for endpoint in list(app.view_functions):
        if not endpoint.startswith(app.config.get("SECURITY_BLUEPRINT_NAME", "security") + "."):
            app.view_functions[endpoint] = stub
    return app


def _session_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = app.csrf_admin_uniquifier
        session["_fresh"] = True
        session["fs_cc"] = "set"
        session["fs_paa"] = time.time()
    return client


def test_there_are_write_routes_to_check(admin_app):
    routes = _write_routes(admin_app)
    assert len(routes) > 40
    assert any(m == "POST" and u == "/api/groups" for m, u, _ in routes)
    assert any(m == "PUT" and u == "/api/groups" for m, u, _ in routes)
    assert any(u == "/api/certificates/revoke_user" for _, u, _ in routes)


def test_session_write_without_token_is_refused_everywhere(admin_app):
    client = _session_client(admin_app)
    let_through = []
    for method, url, endpoint in _write_routes(admin_app):
        r = client.open(url, method=method, json={})
        if r.status_code != 400:
            let_through.append((method, url, endpoint, r.status_code))
    assert let_through == []


def test_session_write_with_csrf_token_is_allowed(admin_app):
    client = _session_client(admin_app)
    token = client.get("/api/login", headers={"Accept": "application/json"}).json["response"][
        "csrf_token"
    ]
    r = client.post("/api/groups", json={}, headers={"X-CSRFToken": token})
    assert r.status_code == STUB_STATUS


def test_token_authenticated_write_is_not_refused_for_csrf(admin_app):
    client = admin_app.test_client()
    refused = []
    for method, url, endpoint in _write_routes(admin_app):
        r = client.open(
            url,
            method=method,
            json={},
            headers={"Authentication-Token": admin_app.csrf_admin_token},
        )
        if r.status_code == 400:
            refused.append((method, url, endpoint))
    assert refused == []


def test_exempt_machine_callbacks_are_not_refused(admin_app):
    client = _session_client(admin_app)
    for path in ("/api/external_auth", "/api/rabbitmq/auth"):
        assert client.post(path, json={}).status_code == STUB_STATUS, path

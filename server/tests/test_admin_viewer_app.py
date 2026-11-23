"""Read-only admins against the real app (conftest's create_app): the fixes from the
security review of the admin_viewer role. See raven/admin_view.py."""

import json

from flask_security import hash_password

from raven.extensions import db


def _users(app, areas, extra=()):
    with app.app_context():
        ds = app.security.datastore
        for role in ("administrator", "user", "admin_viewer"):
            ds.find_or_create_role(role)
        ds.create_user(username="root", password=hash_password("RootPass1!"), roles=["administrator"])
        ds.create_user(
            username="viewer",
            password=hash_password("ViewPass1!"),
            roles=["user", "admin_viewer"],
            admin_view_areas=json.dumps(areas),
        )
        for username, password in extra:
            ds.create_user(username=username, password=hash_password(password), roles=["administrator"])
        db.session.commit()


def _login(app, username, password):
    client = app.test_client()
    token = client.get("/api/login", json={}).json["response"]["csrf_token"]
    r = client.post(
        "/api/login", json={"username": username, "password": password}, headers={"X-CSRFToken": token}
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    return client, token


def test_viewer_keeps_their_own_csrf_token(app):
    _users(app, ["users"])
    viewer, _ = _login(app, "viewer", "ViewPass1!")
    assert viewer.get("/api/login", json={}).json["response"]["csrf_token"] != "hidden"


def test_viewer_never_sees_device_profile_passwords(app):
    _users(app, ["device_profiles"])
    admin, token = _login(app, "root", "RootPass1!")
    r = admin.post(
        "/api/device-profiles",
        headers={"X-CSRFToken": token},
        json={
            "name": "p1",
            "enabled": True,
            "apply_on_enrollment": True,
            "apply_on_connection": False,
            "target_everyone": True,
            "targets": [],
            "settings": [
                {"key": "caPassword", "value": "SuperSecret123", "value_class": "String"},
                {"key": "locationCallsign", "value": "ALPHA", "value_class": "String"},
            ],
        },
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    viewer, _ = _login(app, "viewer", "ViewPass1!")
    listing = viewer.get("/api/device-profiles").get_data(as_text=True)
    preview = viewer.get("/api/device-profiles/preview?phase=enrollment").get_data(as_text=True)
    for body in (listing, preview):
        assert "SuperSecret123" not in body
        assert "ALPHA" in body


def test_viewer_cannot_call_takgov_endpoints_that_rotate_the_token(app):
    _users(app, ["takgov"])
    viewer, _ = _login(app, "viewer", "ViewPass1!")
    assert viewer.get("/api/takgov/plugins").status_code == 403
    assert viewer.get("/api/takgov/products?product_version=5.5.0").status_code == 403
    assert viewer.get("/api/takgov/icon?icon_url=https://tak.gov/eud_api/x.png").status_code == 403


def test_viewer_does_not_get_the_security_posture_report(app):
    _users(app, ["server_requests"], extra=[("opsadmin", "password")])
    viewer, _ = _login(app, "viewer", "ViewPass1!")
    assert viewer.get("/api/security/audit", json={}).status_code == 403
    assert viewer.get("/api/security/audit/download", json={}).status_code == 403
    assert viewer.get("/api/security/audit/events", json={}).status_code == 200


def test_viewer_sees_no_ips_through_the_real_app(app):
    _users(app, ["users"])
    with app.app_context():
        root = app.security.datastore.find_user(username="root")
        root.current_login_ip = "203.0.113.9"
        db.session.commit()
    viewer, _ = _login(app, "viewer", "ViewPass1!")
    body = viewer.get("/api/users?username=root", json={}).get_data(as_text=True)
    assert "203.0.113.9" not in body


def test_viewer_cannot_sort_by_ip(app):
    _users(app, ["users"])
    viewer, _ = _login(app, "viewer", "ViewPass1!")
    assert viewer.get("/api/users?sort_by=current_login_ip", json={}).status_code == 403


def test_role_change_clears_admin_areas(app):
    _users(app, ["users", "groups"])
    admin, token = _login(app, "root", "RootPass1!")
    r = admin.post(
        "/api/user/role", headers={"X-CSRFToken": token}, json={"username": "viewer", "roles": ["user"]}
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        assert app.security.datastore.find_user(username="viewer").admin_view_areas is None


def test_takgov_icon_url_must_stay_under_eud_api(app):
    from raven.blueprints.raven_api.tak_gov_link_api import _is_eud_api_url

    assert _is_eud_api_url("https://tak.gov/eud_api/x/icon.png")
    for url in (
        "https://tak.gov/eud_api/../../admin",
        "https://tak.gov/eud_api/%2e%2e/%2e%2e/admin",
        "https://tak.gov.example/eud_api/a",
        "https://u:p@tak.gov/eud_api/a",
        "http://tak.gov/eud_api/a",
    ):
        assert not _is_eud_api_url(url), url


def test_text_secrets_and_odd_ip_forms():
    from raven.admin_view import redact, redact_secrets, redact_text_secrets

    text = redact_text_secrets("auth failed password=hunter2 Authorization: Bearer abc.def")
    assert "hunter2" not in text and "abc.def" not in text
    assert "hunter2" not in redact_text_secrets('{"password": "hunter2"}')
    out = redact({"10.1.2.3": 1, "u": "a%2F%2F10.1.2.3%3A80", "ip": "010.001.002.003"})
    assert "10.1.2.3" not in json.dumps(out) and "010.001" not in json.dumps(out)
    assert redact_secrets({"keystorePassword": "k", "accessToken": "t", "has_password": True}) == {
        "keystorePassword": "hidden",
        "accessToken": "hidden",
        "has_password": True,
    }

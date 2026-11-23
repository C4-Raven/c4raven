"""Smoke tests over the shared CLI-mode app (tests/conftest.py)."""

import base64


def test_marti_api_clientendpoints(app):
    # Locked down: no client certificate and no web session -> 401
    r = app.test_client().get("/Marti/api/clientEndPoints")
    assert r.status_code == 401


def test_marti_api_clientendpoints_logged_in(auth):
    r = auth.get("/Marti/api/clientEndPoints")
    assert r.status_code == 200


def test_mart_api_tls_config(auth, client):
    credentials = base64.b64encode(b"TestUser:TestPass").decode("ascii")
    r = client.get("/Marti/api/tls/config", headers={"Authorization": f"Basic {credentials}"})
    assert r.status_code == 200


def test_points(auth):
    r = auth.get("/api/point")
    assert r.status_code == 200


def test_me(auth):
    r = auth.get("/api/me")
    assert r.json["username"] == "TestUser"

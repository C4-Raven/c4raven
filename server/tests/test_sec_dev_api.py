"""
Security fixes outside the streaming handler: enrollment (inactive users, weak CSR keys),
CITrap uploads/deletes, the onboarding truststore password, the cert package zip and
federated group routing.
"""

import base64
import io
import json
import os
import zipfile

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from OpenSSL import crypto

from raven.extensions import db


def _csr(key):
    req = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "alice")]))
        .sign(key, hashes.SHA256())
    )
    return crypto.load_certificate_request(
        crypto.FILETYPE_PEM, req.public_bytes(serialization.Encoding.PEM)
    )


# ---------------------------------------------------------------- CSR key size


def test_csr_key_sizes(app):
    from raven.blueprints.marti_api.certificate_enrollment_api import csr_key_problem

    assert csr_key_problem(_csr(rsa.generate_private_key(65537, 2048))) is None
    assert csr_key_problem(_csr(rsa.generate_private_key(65537, 3072))) is None
    assert "1024" in csr_key_problem(_csr(rsa.generate_private_key(65537, 1024)))
    assert csr_key_problem(_csr(ec.generate_private_key(ec.SECP256R1()))) is None
    assert csr_key_problem(_csr(ec.generate_private_key(ec.SECP384R1()))) is None
    assert "224" in csr_key_problem(_csr(ec.generate_private_key(ec.SECP224R1())))


def test_sign_client_refuses_weak_csr_before_signing(app, monkeypatch):
    from raven.blueprints.marti_api import certificate_enrollment_api as enroll

    signed = []
    monkeypatch.setattr(enroll, "basic_auth", lambda *a, **k: "alice")
    monkeypatch.setattr(
        enroll.CertificateAuthority, "sign_csr", lambda *a, **k: signed.append(1) or b""
    )
    key = rsa.generate_private_key(65537, 1024)
    req = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "alice")]))
        .sign(key, hashes.SHA256())
    )
    body = req.public_bytes(serialization.Encoding.PEM)
    with app.test_request_context(
        "/Marti/api/tls/signClient/v2?clientUid=ANDROID-x", method="POST", data=body
    ):
        response = enroll.sign_csr_v2()
    assert response[1] == 400
    assert signed == []


# ---------------------------------------------------------------- deactivated users


def _basic(username, password):
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def test_deactivated_user_cannot_use_enrollment_credentials(app):
    from flask_security import hash_password

    from raven.blueprints.marti_api.certificate_enrollment_api import basic_auth

    with app.app_context():
        ds = app.security.datastore
        ds.find_or_create_role("user")
        ds.create_user(username="carol", password=hash_password("CarolPassword123"), roles=["user"])
        ds.create_user(
            username="dave", password=hash_password("DavePassword123"), roles=["user"], active=False
        )
        db.session.commit()

    with app.test_request_context("/"):
        assert basic_auth(_basic("carol", "CarolPassword123")) == "carol"
        assert basic_auth(_basic("dave", "DavePassword123")) is False


# ---------------------------------------------------------------- onboarding truststore password


def test_onboard_truststore_password_not_on_command_line(app, monkeypatch, tmp_path):
    from raven.blueprints.raven_api import onboard_api

    (tmp_path / "ca.pem").write_text("x")
    seen = {}

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs.get("env") or {}
        out = command[command.index("-out") + 1]
        with open(out, "wb") as f:
            f.write(b"p12")
        return Result()

    monkeypatch.setattr(onboard_api.subprocess, "run", fake_run)
    assert onboard_api.export_onboard_truststore(str(tmp_path), "s3cret-pass", "CA") == b"p12"
    assert not any("s3cret-pass" in part for part in seen["command"])
    assert "env:RAVEN_ONBOARD_TRUSTSTORE_PASS" in seen["command"]
    assert seen["env"]["RAVEN_ONBOARD_TRUSTSTORE_PASS"] == "s3cret-pass"


# ---------------------------------------------------------------- CITrap


def _report_zip(title="Ambush", report_id="r-1", padding=0):
    xml = (
        f'<report id="{report_id}" title="{title}" type="SPOTREP" '
        f'dateTime="2026-11-15T10:00:00.000Z" location="POINT (2 1)"/>'
    )
    if padding:
        xml += "<!--" + "x" * padding + "-->"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("abc/report.xml", xml)
    return buf.getvalue()


def test_citrap_report_xml_size_is_capped(app, client, monkeypatch):
    from raven.blueprints.marti_api import citrap_api

    monkeypatch.setattr(citrap_api, "MAX_REPORT_XML_BYTES", 1000)
    monkeypatch.setattr(citrap_api, "gettext", lambda s, **k: s)
    r = client.post("/Marti/api/citrap?clientUid=ANDROID-a", data=_report_zip(padding=5000))
    assert r.status_code == 413


def test_citrap_reports_with_the_same_title_keep_their_own_files(app):
    from bs4 import BeautifulSoup

    from raven.blueprints.marti_api.citrap_api import _report_file_name

    def report(**attrs):
        tag = BeautifulSoup("<report/>", "xml").find("report")
        tag.attrs.update(attrs)
        return tag

    a = _report_file_name(report(title="Ambush", id="r-1"))
    b = _report_file_name(report(title="Ambush", id="r-2"))
    assert a != b and a.startswith("Ambush") and a.endswith(".zip")
    # Re-submitting the same report replaces its own file
    assert _report_file_name(report(title="Ambush", id="r-1")) == a
    assert "/" not in _report_file_name(report(title="../../etc/passwd", id="../x"))
    assert _report_file_name(report()).endswith(".zip")


def test_citrap_delete_needs_authentication(app, client, monkeypatch):
    from raven.blueprints.marti_api import citrap_api

    monkeypatch.setattr(citrap_api, "gettext", lambda s, **k: s)
    r = client.delete("/Marti/api/citrap/some-id")
    assert r.status_code == 401


# ---------------------------------------------------------------- cert package zip


def test_zip_tree_names_entries_relative_without_chdir(app, tmp_path):
    from raven.certificate_authority import CertificateAuthority

    base = tmp_path / "certs" / "alice"
    (base / "5c2b" / "sub").mkdir(parents=True)
    (base / "5c2b" / "preference.pref").write_text("p")
    (base / "5c2b" / "sub" / "x.p12").write_text("x")
    (base / "MANIFEST").mkdir()
    (base / "MANIFEST" / "manifest.xml").write_text("m")

    ca = object.__new__(CertificateAuthority)
    ca.logger = __import__("logging").getLogger("test")
    cwd = os.getcwd()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        ca._zip_tree(z, str(base), "5c2b")
        ca._zip_tree(z, str(base), "MANIFEST", log=True)
    assert os.getcwd() == cwd
    names = zipfile.ZipFile(buf).namelist()
    assert sorted(names) == ["5c2b/preference.pref", "5c2b/sub/x.p12", "MANIFEST/manifest.xml"]


def test_certificate_authority_no_longer_chdirs(app):
    import inspect

    from raven import certificate_authority

    assert "os.chdir(" not in inspect.getsource(certificate_authority)


# ---------------------------------------------------------------- federated group routing


def test_allowed_target_groups_keeps_existing_shared_groups_only(app):
    import time

    from raven.controllers import federation_hub_daemon as fhd

    bridge = object.__new__(fhd.FigFederateClient)
    bridge._local_groups = {"Fed", "Cyan", "Link: alice and bob", "__uv__alice__bob", "__ANON__"}
    bridge._local_groups_at = time.monotonic()
    bridge._rejected_group_logged = {}

    allowed = bridge._allowed_target_groups(
        ["Fed", "Cyan", "Link: alice and bob", "__uv__alice__bob", "__ANON__", "Nope", "Fed"],
        "peer",
    )
    assert allowed == ["Fed", "Cyan"]
    assert ("peer", "Nope") in bridge._rejected_group_logged


def test_federated_event_to_a_link_group_is_not_routed_there(app, monkeypatch):
    from tests.test_federated_eud_status import SA, T, UID, _bridge
    from raven.proto.federation import fig_pb2
    from raven.controllers import federation_hub_daemon as fhd

    remote = _bridge(monkeypatch)
    remote._on_outbound_message({"uid": UID, "cot": SA.format(uid=UID, t=T)}, "Fed")
    event = list(remote._outbound_queue.queue)[0].event

    bridge = _bridge(monkeypatch)
    monkeypatch.setattr(bridge, "_local_group_names", lambda: {"Fed", "Link: alice and bob"}, raising=False)

    # Legitimate configured group: routed as before
    bridge._handle_federated_event(fig_pb2.FederatedEvent(event=event, federateGroups=["Fed"]), None)
    message = json.loads(bridge.published[-1])
    assert message["target_groups"] == ["Fed"] and not message["target_groups_rejected"]

    # Only a local-only group named: no target groups, and cot_parser must not fall back
    bridge._injected = fhd._RoundTripLog()
    bridge._handle_federated_event(
        fig_pb2.FederatedEvent(event=event, federateGroups=["Link: alice and bob"]), None
    )
    message = json.loads(bridge.published[-1])
    assert message["target_groups"] == [] and message["target_groups_rejected"]


class _Channel:
    def __init__(self):
        self.published = []

    def basic_publish(self, exchange, routing_key, body, properties=None):
        self.published.append((exchange, routing_key))


@pytest.fixture
def cot_controller(app):
    from bs4 import BeautifulSoup

    from raven.cot_parser.cot_parser import CoTController
    from raven.extensions import logger

    c = CoTController(app.app_context(), logger, db, None)
    c.rabbit_channel = _Channel()
    return c, lambda xml: BeautifulSoup(xml, "xml").find("event")


SA_XML = (
    '<event version="2.0" uid="S-1" type="a-f-G" how="m-g" time="2026-11-15T10:00:00Z" '
    'start="2026-11-15T10:00:00Z" stale="2026-11-15T10:00:00Z"><point lat="1" lon="2" hae="0" '
    'ce="9" le="9"/><detail/></event>'
)


def test_route_cot_federated_groups(cot_controller):
    c, event_of = cot_controller

    c.route_cot(event_of(SA_XML), "S-1", 1, ["Fed", "Link: alice and bob"])
    assert c.rabbit_channel.published == [("groups", "Fed.OUT")]

    c.rabbit_channel.published.clear()
    c.route_cot(event_of(SA_XML), "S-1", 1, [], True)
    assert c.rabbit_channel.published == []

    c.rabbit_channel.published.clear()
    c.route_cot(event_of(SA_XML), "S-1", 1, ["__uv__alice__bob"])
    assert c.rabbit_channel.published == []

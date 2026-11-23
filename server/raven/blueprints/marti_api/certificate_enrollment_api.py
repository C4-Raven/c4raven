"""TAK certificate enrollment (port 8446 through nginx): the TLS config probe, CSR signing
(signClient v1/v2), the Basic credential check shared with the device profile routes, the CSR key
policy and the "this device was revoked, it may not enroll again" check.

sign_csr_v2 looks basic_auth and CertificateAuthority up through this module's globals when it
runs, so tests can replace either here.
"""

import base64
import datetime
import json
import os
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, SubElement, tostring

import bleach
import sqlalchemy.exc
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, rsa
from cryptography.x509.oid import NameOID
from flask import Blueprint, Response
from flask import current_app as app
from flask import g, jsonify, request
from OpenSSL import crypto
from sqlalchemy import select

from raven.audit import record_audit_event
from raven.cert_revocation import serial_from_pem
from raven.cert_validity import resolve_cert_days
from raven.certificate_authority import CertificateAuthority
from raven.db_upsert import insert_ignore
from raven.extensions import db, ldap_manager, logger
from raven.models.Certificate import Certificate
from raven.models.EnrollmentBlock import EnrollmentBlock
from raven.models.EUD import EUD

certificate_authority_api_blueprint = Blueprint("certificate_authority_api_blueprint", __name__)

MIN_RSA_BITS = 2048
MIN_EC_BITS = 256

_CERT_HEADER = "-----BEGIN CERTIFICATE-----\n"
_CERT_TRAILER = "\n-----END CERTIFICATE-----\n"
_CSR_HEADER = "-----BEGIN CERTIFICATE REQUEST-----"
_CSR_TRAILER = "-----END CERTIFICATE REQUEST-----"

_BLOCKED_MESSAGE = (
    "This device's certificate was revoked. An administrator must allow it to enroll again before it "
    "can get a new certificate."
)


def _decode_credentials(credentials: str) -> tuple[str, str]:
    encoded = credentials.split(" ", 1)[1] if " " in credentials else credentials
    username, password = base64.b64decode(encoded).decode("utf-8").split(":", 1)
    return bleach.clean(username), bleach.clean(password)


def _ldap_login(username: str, password: str):
    result = ldap_manager.authenticate(username, password)
    from flask_ldap3_login import AuthenticationResponseStatus

    if result.status != AuthenticationResponseStatus.success:
        return False

    from raven.blueprints.raven_api.ldap_api import save_user

    user = save_user(result.user_dn, result.user_id, result.user_info, result.user_groups)
    if user is not None and not user.active:
        return False
    g.raven_auth_method = "ldap"
    return result.user_id or username


def basic_auth(credentials, consume_token=True, check_token_max=True):
    """Checks an HTTP Basic Authorization header value.

    The password may be the account's password or an enrollment token (QR enrollment).

    :returns: the account's username, or False. Never raises.
    """
    g.raven_auth_method = None
    g.raven_auth_username = None
    if not credentials:
        return False

    try:
        username, password = _decode_credentials(credentials)
        g.raven_auth_username = username

        if app.config.get("RAVEN_ENABLE_LDAP"):
            return _ldap_login(username, password)

        from flask_security import verify_password

        user = app.security.datastore.find_user(username=username)
        if not user:
            return False
        # Before the token check, so a refused attempt never uses up a token
        if not user.active:
            return False

        if verify_password(password, user.password):
            g.raven_auth_method = "password"
            return user.username

        from raven.models.Token import Token

        if Token.verify_token(password, user.username, consume=consume_token, check_max=check_token_max):
            g.raven_auth_method = "token"
            return user.username

        return False
    except BaseException as e:
        logger.debug(f"Basic authentication failed: {e}")
        return False


def csr_key_problem(csr_req):
    """Why the CSR's public key is too weak to sign, or None when it's fine."""
    try:
        key = csr_req.get_pubkey().to_cryptography_key()
    except BaseException as e:
        return f"unreadable public key ({e})"

    if isinstance(key, rsa.RSAPublicKey):
        if key.key_size < MIN_RSA_BITS:
            return f"RSA key is {key.key_size} bits, at least {MIN_RSA_BITS} required"
        return None
    if isinstance(key, ec.EllipticCurvePublicKey):
        if key.curve.key_size < MIN_EC_BITS:
            return f"EC key is {key.curve.key_size} bits, at least {MIN_EC_BITS} required"
        return None
    if isinstance(key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
        return None
    return f"unsupported key type {type(key).__name__}"


def enrollment_block(uid):
    """The EnrollmentBlock for this device uid, or None."""
    if not uid:
        return None
    return db.session.get(EnrollmentBlock, uid)


def enrollment_blocked_response(uid, username, via):
    """None if the device may enroll, else a (response, 403) to return (and the attempt is audited)."""
    block = enrollment_block(uid)
    if block is None:
        return None

    record_audit_event(
        "enrollment",
        "enroll_blocked",
        username=username,
        success=False,
        details=(
            f"Device {uid} tried to enroll ({via}) but its certificate {block.serial_number or ''} was revoked; "
            "an admin must allow it to enroll again"
        ),
    )
    return jsonify({"success": False, "error": _BLOCKED_MESSAGE}), 403


@certificate_authority_api_blueprint.route("/Marti/api/tls/config", methods=["GET"])
def tls_config():
    root = Element(
        "ns2:certificateConfig",
        {"xmlns": "http://bbn.com/marti/xml/config", "xmlns:ns2": "com.bbn.marti.config"},
    )
    entries = SubElement(root, "nameEntries")
    SubElement(entries, "nameEntry", {"name": "O", "value": str(app.config.get("RAVEN_CA_ORGANIZATION"))})
    SubElement(entries, "nameEntry", {"name": "OU", "value": str(app.config.get("RAVEN_CA_ORGANIZATIONAL_UNIT"))})
    return Response(tostring(root).decode(), status=200, content_type="text/plain; charset=UTF-8")


@certificate_authority_api_blueprint.route("/Marti/api/tls/signClient/", methods=["POST"])
def sign_csr():
    if not basic_auth(request.headers.get("Authorization")):
        return "", 401
    return "", 200


def _normalize_csr(body: str) -> bytes:
    if _CSR_HEADER not in body:
        body = f"{_CSR_HEADER}\n{body}"
    if not body.endswith("\n"):
        body += "\n"
    body += _CSR_TRAILER
    return body.encode()


def _strip_pem(pem: str) -> str:
    return pem.replace(_CERT_HEADER, "").replace(_CERT_TRAILER, "")


def _subject_common_names(csr_req) -> list[str]:
    subject = csr_req.to_cryptography().subject
    return [attribute.value for attribute in subject.get_attributes_for_oid(NameOID.COMMON_NAME)]


def _record_certificate(uid, username, user, cert_days, serial):
    """Stores the issued cert (and the device, if new). Revoked rows are never reused."""
    callsign = None
    if uid:
        insert_ignore(db.session, EUD, {"uid": uid, "user_id": user.id if user else None}, ["uid"])
        eud = db.session.execute(select(EUD).filter_by(uid=uid)).scalar_one()
        if eud.user_id is None and user is not None:
            eud.user_id = user.id
        callsign = eud.callsign
        db.session.commit()

    ca_folder = app.config.get("RAVEN_CA_FOLDER")
    values = {
        "common_name": username,
        "username": user.username if user else None,
        "eud_uid": uid,
        "callsign": callsign,
        "expiration_date": datetime.datetime.now() + datetime.timedelta(days=cert_days),
        "server_address": urlparse(request.url_root).hostname,
        "server_port": app.config.get("RAVEN_MARTI_HTTPS_PORT"),
        "truststore_filename": os.path.join(ca_folder, "truststore-root.p12"),
        "user_cert_filename": os.path.join(ca_folder, "certs", username, f"{username}.pem"),
        "csr": os.path.join(ca_folder, "certs", username, f"{username}.csr"),
        "serial_number": serial,
        "revoked_at": None,
        "revocation_reason": None,
    }

    try:
        db.session.add(Certificate(**values))
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        # An old database with a unique certificates.eud_uid: reuse the device's current row
        db.session.rollback()
        existing = db.session.execute(
            select(Certificate)
            .filter(Certificate.eud_uid == uid, Certificate.revoked_at.is_(None))
            .order_by(Certificate.id.desc())
        ).scalar()
        if existing is None:
            raise
        for key, value in values.items():
            setattr(existing, key, value)
        db.session.commit()

    return callsign


def _enrollment_response(signed_cert: str, ca: str):
    accept = request.headers.get("Accept")
    body = {"signedCert": signed_cert, "ca0": ca, "ca1": ca}

    if accept == "text/plain":
        return Response(
            json.dumps(body), status=200, content_type="text/plain", headers={"Content-Encoding": "charset=UTF-8"}
        )
    if accept in ("application/json", "*/*"):
        return Response(json.dumps(body), status=200, content_type="application/json")
    if accept is None:
        return Response(
            json.dumps(body),
            status=200,
            content_type="application/xml",
            headers={"Content-Encoding": "charset=UTF-8"},
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<enrollment><signedCert>{signed_cert}</signedCert><ca>{ca}</ca></enrollment>"
    )
    return Response(xml, status=200, content_type="application/xml", headers={"Content-Encoding": "charset=UTF-8"})


@certificate_authority_api_blueprint.route("/Marti/api/tls/signClient/v2", methods=["POST"])
def sign_csr_v2():
    uid = request.args.get("clientUID") or request.args.get("clientUid")

    username = basic_auth(request.headers.get("Authorization"))
    if not username:
        record_audit_event(
            "enrollment",
            "enroll_failed",
            username=getattr(g, "raven_auth_username", None),
            success=False,
            details=f"Certificate enrollment rejected (bad credentials), device {uid}",
        )
        return "", 401
    auth_method = getattr(g, "raven_auth_method", None)

    blocked = enrollment_blocked_response(uid, username, "signClient")
    if blocked is not None:
        return blocked

    try:
        csr_bytes = _normalize_csr(request.get_data(as_text=True))
        csr_req = crypto.load_certificate_request(crypto.FILETYPE_PEM, csr_bytes)

        problem = csr_key_problem(csr_req)
        if problem:
            record_audit_event(
                "enrollment",
                "enroll_failed",
                username=username,
                success=False,
                details=f"Certificate enrollment rejected for device {uid}: {problem}",
            )
            return jsonify({"success": False, "error": problem}), 400

        # The cert is always for the authenticated account, whatever CN the CSR asks for
        subject = None
        if _subject_common_names(csr_req) != [username]:
            subject = f"{app.config.get('RAVEN_CA_SUBJECT')}/CN={CertificateAuthority.escape_subject_value(username)}"

        user = app.security.datastore.find_user(username=username)
        cert_days = resolve_cert_days(user)

        ca = CertificateAuthority(logger, app)
        pem = ca.sign_csr(csr_bytes, username, False, subject=subject, days=cert_days)
        serial = serial_from_pem(pem)

        callsign = _record_certificate(uid, username, user, cert_days, serial)

        if auth_method == "token":
            action, method_text = "enroll_qr", "a QR enrollment token"
        else:
            action, method_text = "enroll_credentials", auth_method or "credentials"
        record_audit_event(
            "enrollment",
            action,
            username=username,
            success=True,
            details=(
                f"Device {uid} (callsign {callsign if uid else None}) enrolled with {method_text}, "
                f"cert valid {cert_days} days"
            ),
        )

        if isinstance(pem, bytes):
            pem = pem.decode()
        with open(os.path.join(app.config.get("RAVEN_CA_FOLDER"), "ca.pem"), "r") as f:
            ca_pem = f.read()

        return _enrollment_response(_strip_pem(pem), _strip_pem(ca_pem))
    except BaseException as e:
        logger.error(f"Certificate enrollment failed for {username}: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

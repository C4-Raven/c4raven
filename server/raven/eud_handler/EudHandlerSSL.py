"""A TLS streaming connection (port 8089): the handshake runs here, in the forked child."""

import ssl
import traceback

from raven import cert_revocation
from raven.eud_handler.EudHandler import EudHandler


def handshake_failure_reason(e) -> str:
    """One readable reason for a failed handshake (scanners, plain HTTP, no client cert, ...)."""
    if isinstance(e, ssl.SSLCertVerificationError) and getattr(e, "verify_message", None):
        return f"{e.reason}: {e.verify_message}"
    if isinstance(e, ssl.SSLError) and getattr(e, "reason", None):
        return e.reason
    return f"{type(e).__name__}: {e}"


# Handshakes that never got as far as a client certificate: port scanners, plain HTTP,
# uptime monitors that open the port and hang up, clients from another protocol era. They
# are logged at INFO so they stay on record without filling the Error Log's warnings.
# Anything certificate-related (no certificate, unknown CA, expired, ...) stays a WARNING:
# that can be a real device that needs re-enrolling.
PROBE_REASONS = (
    "UNEXPECTED_EOF_WHILE_READING",
    "EOF occurred in violation of protocol",
    "HTTP_REQUEST",
    "HTTPS_PROXY_REQUEST",
    "WRONG_VERSION_NUMBER",
    "UNSUPPORTED_PROTOCOL",
    "NO_SUITABLE_KEY_SHARE",
    "NO_SHARED_CIPHER",
    "VERSION_TOO_LOW",
    "TLSV1_ALERT_PROTOCOL_VERSION",
    "UNKNOWN_PROTOCOL",
    "ConnectionResetError",
    "BrokenPipeError",
    "TimeoutError",
)


def is_probe(reason: str) -> bool:
    """True when a handshake failure looks like a scanner or monitor, not a TAK client."""
    return any(probe in reason for probe in PROBE_REASONS)


class EudHandlerSSL(EudHandler):
    is_ssl = True

    def setup(self):
        super().setup()
        ip = self._client_ip()

        try:
            self.request.settimeout(10)
            self.request.do_handshake()
            self.request.settimeout(None)
        except OSError as e:
            reason = handshake_failure_reason(e)
            log = self.logger.info if is_probe(reason) else self.logger.warning
            log(f"TLS handshake with {ip} failed: {reason}")
            self.close_connection()
            return

        try:
            cert = self.request.getpeercert()
            self.cert_serial = cert_revocation.normalize_serial(cert["serialNumber"])
            if cert_revocation.is_revoked(self.app.config.get("RAVEN_CA_FOLDER"), self.cert_serial):
                self.logger.warning(f"Refused revoked certificate {self.cert_serial} from {ip}")
                self.close_connection()
                return

            self.request_cert_control_queue()

            for rdn in cert.get("subject", ()):
                for key, value in rdn:
                    if key != "commonName":
                        continue
                    self.common_name = value
                    with self.app.app_context():
                        self.user = self.app.security.datastore.find_user(username=self.common_name)
                        self.handle_auth("")
        except Exception as e:
            self.logger.error(f"Failed to set up the connection from {ip}: {type(e).__name__}: {e}")
            self.logger.error(traceback.format_exc())
            self.close_connection()

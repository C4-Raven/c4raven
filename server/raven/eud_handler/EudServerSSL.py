"""TLS streaming server (port 8089).

The listening socket is plain TCP; every accepted connection is wrapped with the current SSL
context, so a regenerated CRL (a revoked certificate) takes effect on the next handshake without a
restart. The handshake itself runs in the forked child (EudHandlerSSL.setup)."""

import datetime
import os
import ssl
import time

from raven import cert_revocation
from raven.eud_handler.EudServer import EudServer


class EudServerSSL(EudServer):
    CRL_CHECK_INTERVAL = 3600

    # Also the state of a server built with __new__ (tests)
    _ssl_context = None
    _crl_key = None
    _crl_checked_at = None

    def __init__(self, server_address, eud_handler, logger, app_context):
        super().__init__(server_address, eud_handler, logger, app_context)
        try:
            self._refresh_ssl_context()
        except BaseException:
            self.server_close()
            raise

    # ------------------------------------------------------------------ accept

    def get_request(self):
        sock, address = self.socket.accept()
        try:
            context = self._refresh_ssl_context()
            tls = context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        except BaseException:
            sock.close()
            raise
        return tls, address

    # ------------------------------------------------------------------ context

    def _ca_folder(self):
        return self.app_context.config.get("RAVEN_CA_FOLDER")

    @staticmethod
    def _read_crl_key(ca_folder):
        try:
            st = os.stat(os.path.join(ca_folder, "ca.crl"))
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size, st.st_ino

    def _check_crl_expiry(self, ca_folder) -> bool:
        """Regenerates a CRL that expired or expires within CRL_REFRESH_DAYS. True if rewritten."""
        next_update = cert_revocation.crl_next_update(ca_folder)
        if next_update is None:
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        if next_update - now > datetime.timedelta(days=cert_revocation.CRL_REFRESH_DAYS):
            return False
        expired = next_update <= now
        state = "expired" if expired else "expiring"
        if expired:
            self.logger.error(f"The CA's CRL expired at {next_update.isoformat()}, regenerating it")
        else:
            self.logger.warning(f"The CA's CRL expires at {next_update.isoformat()}, regenerating it")
        try:
            return bool(
                cert_revocation.ensure_crl(ca_folder, self.app_context.config.get("RAVEN_CA_PASSWORD"), self.logger)
            )
        except Exception as e:
            cert_revocation.report_crl_failure(
                self.app_context,
                f"Failed to regenerate the {state} CRL (next update {next_update.isoformat()}): {e}",
                self.logger,
            )
            return False

    def _build_ssl_context(self, ca_folder):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("ECDHE+AESGCM:DHE+AESGCM")
        context.options |= ssl.OP_NO_RENEGOTIATION
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(
            certfile=os.path.join(ca_folder, "certs", "raven", "raven.pem"),
            keyfile=os.path.join(ca_folder, "certs", "raven", "raven.nopass.key"),
        )
        context.load_verify_locations(cafile=os.path.join(ca_folder, "ca.pem"))
        crl = os.path.join(ca_folder, "ca.crl")
        if os.path.exists(crl):
            context.load_verify_locations(cafile=crl)
            context.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
        else:
            self.logger.warning(f"{crl} not found: revoked certificates can't be refused on the streaming port")
        return context

    def _refresh_ssl_context(self):
        """The SSL context for the next handshake, rebuilt when ca.crl changed."""
        ca_folder = self._ca_folder()
        key = self._read_crl_key(ca_folder)

        now = time.monotonic()
        due = self._crl_checked_at is None or now - self._crl_checked_at >= self.CRL_CHECK_INTERVAL
        if key is not None and due:
            self._crl_checked_at = now
            try:
                if self._check_crl_expiry(ca_folder):
                    key = self._read_crl_key(ca_folder)
            except Exception as e:
                self.logger.error(f"Failed to check the CRL's expiry: {e}")

        if self._ssl_context is not None and key == self._crl_key:
            return self._ssl_context

        previous = self._ssl_context
        self._crl_key = key
        try:
            context = self._build_ssl_context(ca_folder)
        except Exception as e:
            if previous is None:
                raise
            self.logger.error(f"Failed to reload the SSL context, keeping the previous one: {e}")
            return previous
        if previous is not None:
            self.logger.info("ca.crl changed, reloaded the streaming port's SSL context")
        self._ssl_context = context
        return context

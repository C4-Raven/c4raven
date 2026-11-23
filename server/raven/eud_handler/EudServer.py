"""Plain TCP streaming server (port 8088, off unless RAVEN_ENABLE_TCP_STREAMING_PORT), and the base
of the TLS server. Every accepted connection is handled in its own forked child process."""

import errno
import socketserver
import ssl
import sys

# errno values meaning the device went away (reset, hung up, timed out): not a server bug
CLIENT_GONE_ERRNOS = {errno.ENOTCONN, errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT}


def is_client_disconnect(e: BaseException) -> bool:
    """True when e just means the client dropped or a scanner probed the port."""
    # These come from connections the handler itself made (RabbitMQ, ...), so they are bugs
    if isinstance(e, (ConnectionRefusedError, ssl.SSLCertVerificationError)):
        return False
    if isinstance(e, (ConnectionError, TimeoutError, ssl.SSLError)):
        return True
    if isinstance(e, OSError) and e.errno in CLIENT_GONE_ERRNOS:
        return True
    return False


def plaintext_tcp_enabled(config) -> bool:
    return bool(config.get("RAVEN_ENABLE_TCP_STREAMING_PORT"))


class EudServer(socketserver.ForkingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    port = 8088
    # ForkingMixIn waits for a child to exit before forking another once this many are alive
    max_children = 2000

    def __init__(self, server_address, eud_handler, logger, app_context):
        self.server_address = server_address
        self.logger = logger
        self.app_context = app_context
        try:
            cap = int(app_context.config.get("RAVEN_EUD_MAX_CONNECTIONS") or 0)
        except (AttributeError, TypeError, ValueError):
            cap = 0
        if cap > 0:
            self.max_children = cap
        super().__init__(server_address, eud_handler)

    def server_activate(self):
        if self.logger:
            self.logger.debug(f"Listening on {self.server_address}")
        super().server_activate()

    def server_close(self):
        if self.logger:
            self.logger.debug(f"Closing the server on {self.server_address}")
        super().server_close()

    def process_request(self, request, client_address):
        if self.logger:
            self.logger.debug(f"Connection from {client_address[0]}:{client_address[1]}")
        super().process_request(request, client_address)

    def handle_error(self, request, client_address):
        """Runs in the child when the handler raised. A client that went away is one warning
        line; anything else is an error with its traceback."""
        e = sys.exc_info()[1]
        ip, port = (client_address[0], client_address[1]) if client_address else (None, None)
        if e is not None and is_client_disconnect(e):
            self.logger.warning(f"Connection from {ip}:{port} ended: {type(e).__name__}: {e}")
        else:
            self.logger.error(f"Error handling the connection from {ip}:{port}", exc_info=True)

"""UDP CoT input (port 8087). Each datagram is handled by EudHandler in its own forked child, with
request = (data, socket)."""

import socketserver


class EudServerUdp(socketserver.ForkingUDPServer):
    port = 8087
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, eud_handler, logger, app_context):
        # Handlers reach the logger and Flask app through self.server
        self.logger, self.app_context = logger, app_context
        super().__init__(server_address, eud_handler)

"""Connects Raven to the local Murmur's Ice interface and installs the external authenticator.

The authenticator is set on every booted virtual server, a meta callback covers servers started
later, and a 10-second timer re-attaches so a restarted Murmur gets the authenticator back.
"""

import threading
import uuid

import Ice

from raven.mumble.mumble_authenticator import Murmur, MumbleAuthenticator

META_PROXY = "Meta:tcp -h 127.0.0.1 -p 6502"
CALLBACK_ADAPTER = "Callback.Client"
CALLBACK_ENDPOINTS = "tcp -h 127.0.0.1"
REATTACH_INTERVAL = 10.0


def _is_invalid_secret(error) -> bool:
    if isinstance(error, Murmur.InvalidSecretException):
        return True
    return isinstance(error, Ice.UnknownUserException) and error.unknown == "Murmur::InvalidSecretException"


class MetaCallback(Murmur.MetaCallback):
    def __init__(self, authenticator_app):
        Murmur.MetaCallback.__init__(self)
        self.app = authenticator_app

    def started(self, server, current=None):
        self.app.logger.info("Mumble virtual server started; installing the Raven authenticator")
        try:
            server.setAuthenticator(self.app.auth)
        except (Murmur.InvalidSecretException, Ice.UnknownUserException) as e:
            if not _is_invalid_secret(e):
                raise
            self.app.logger.error("Invalid Ice secret when attaching the authenticator to a new Mumble server")

    def stopped(self, server, current=None):
        try:
            self.app.logger.info("Mumble virtual server {} stopped".format(server.id()))
        except Ice.ConnectionRefusedException:
            self.app.connected = False
        except Exception:  # noqa: BLE001
            self.app.logger.info("A Mumble virtual server stopped")


class MumbleIceApp(Ice.Application):
    def __init__(self, app, logger, ice):
        super().__init__()
        self.app, self.logger, self.ice = app, logger, ice
        # Filled in by run() once Murmur's Meta interface answers
        self.meta = self.adapter = self.metacb = self.auth = self.watchdog = None
        self.connected = False

    def run(self, args=None):
        try:
            base = self.ice.stringToProxy(META_PROXY)
            self.meta = Murmur.MetaPrx.checkedCast(base)
        except Ice.ConnectionRefusedException:
            self.logger.error("Mumble Ice interface refused the connection on 127.0.0.1:6502")
            return 1
        if self.meta is None:
            self.logger.error("Mumble Ice endpoint is not a Murmur Meta interface")
            return 1

        self.adapter = self.ice.createObjectAdapterWithEndpoints(CALLBACK_ADAPTER, CALLBACK_ENDPOINTS)
        self.adapter.activate()
        self.metacb = Murmur.MetaCallbackPrx.uncheckedCast(
            self.adapter.add(MetaCallback(self), Ice.stringToIdentity(str(uuid.uuid4())))
        )
        self.auth = Murmur.ServerUpdatingAuthenticatorPrx.uncheckedCast(
            self.adapter.add(
                MumbleAuthenticator(self.app, self.logger, self.ice), Ice.stringToIdentity(str(uuid.uuid4()))
            )
        )

        if not self.attach_callbacks():
            return 1

        self.logger.info("Mumble authenticator attached")
        # Keep re-attaching: Murmur forgets the authenticator when it restarts
        self._arm_watchdog()
        return 0

    def _arm_watchdog(self):
        self.watchdog = threading.Timer(REATTACH_INTERVAL, self._watchdog_tick)
        self.watchdog.daemon = True
        self.watchdog.start()

    def _watchdog_tick(self):
        try:
            self.attach_callbacks(quiet=True)
        except Exception as e:  # noqa: BLE001
            self.logger.debug("Mumble re-attach failed: {}".format(e))
        finally:
            self._arm_watchdog()

    def attach_callbacks(self, quiet=False) -> bool:
        try:
            self.meta.addCallback(self.metacb)
            for server in self.meta.getBootedServers():
                server.setAuthenticator(self.auth)
        except (Murmur.InvalidSecretException, Ice.UnknownUserException, Ice.ConnectionRefusedException) as e:
            if isinstance(e, Ice.ConnectionRefusedException):
                if not quiet:
                    self.logger.warning("Mumble Ice interface refused the connection while attaching")
            elif _is_invalid_secret(e):
                self.logger.error("Invalid Ice secret: Murmur's icesecretwrite must be empty")
            else:
                raise
            self.connected = False
            return False
        self.connected = True
        return True


class MumbleIceDaemon(threading.Thread):
    def __init__(self, app, logger):
        super().__init__(daemon=True)
        self.app = app
        self.logger = logger
        self.ice_app = None

    def run(self):
        init_data = Ice.InitializationData()
        init_data.properties = Ice.createProperties([])
        init_data.properties.setProperty("Ice.ImplicitContext", "Shared")
        init_data.properties.setProperty("Ice.Default.EncodingVersion", "1.0")
        init_data.properties.setProperty("Ice.Default.InvocationTimeout", "30000")
        init_data.properties.setProperty("Ice.MessageSizeMax", "1024")
        # The communicator is deliberately left running: the servants must keep serving Murmur.
        ice = Ice.initialize(init_data)

        self.ice_app = MumbleIceApp(self.app, self.logger, ice)
        try:
            self.ice_app.run()
        except Exception as e:  # noqa: BLE001
            self.logger.error("Mumble authenticator stopped: {}".format(e))

"""One streaming connection from a TAK device (ATAK, WinTAK, iTAK, radio gateways).

Runs in its own forked child process (see EudServer), with two threads: the handler thread
(recv, framing, handle_cot, pings, clear commands, offline chat) and the pika ioloop thread
(on_message, control messages). All socket writes go through send_lock. RabbitMQ calls the
handler thread makes are handed to the ioloop thread when it is running.

What a device sends is published to RabbitMQ (firehose + cot_parser); what RabbitMQ routes to the
device's uid / callsign queues is written back to it.
"""

import base64
import codecs
import contextlib
import json
import os
import random
import re
import socket
import socketserver
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

import bleach
import pika
import sqlalchemy.exc
from bs4 import BeautifulSoup
from flask import Flask, has_app_context
from lxml import etree
from sqlalchemy import select, update

from raven import cert_revocation, metrics, offline_chat, remote_clear
from raven.extensions import db, ldap_manager
from raven.functions import iso8601_string_from_datetime
from raven.extensions import logger as raven_logger

# <takv platform> of devices relayed over RF: no RabbitMQ queues, no supersede, owner kept
RELAY_PLATFORMS = ("Meshtastic", "DMRCOT")

# Characters buffered without a closing tag before the connection is dropped. Read at run time
# (tests patch it).
MAX_PENDING_COT_CHARS = 2 * 1024 * 1024

_CLOSING_TAGS = ("</event>", "</auth>")
_PASSWORD_RE = re.compile(r"""(password\s*=\s*)(["'])(.*?)\2""", re.S | re.I)
_XML_CHECK_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)

# Exchange arguments must match raven app.py's declarations, or the broker closes the channel (406)
_DECLARED_EXCHANGES = (
    ("flask-socketio", "fanout", False),
    ("eud_control", "direct", True),
)


def build_flask_app(import_name, configure_logging=None):
    """The Flask app a standalone EUD handler process uses for DB and auth access."""
    import flask_wtf
    import yaml
    from flask_security import Security, SQLAlchemyUserDatastore
    from flask_security.models import fsqla_v3 as fsqla

    from raven.config_file import write_default_config
    from raven.defaultconfig import DefaultConfig

    app = Flask(import_name)
    app.config.from_object(DefaultConfig)
    config_path = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "config.yml")
    if os.path.exists(config_path):
        app.config.from_file(config_path, load=yaml.safe_load)
    else:
        raven_logger.info("Creating config.yml")
        write_default_config(config_path)

    if configure_logging:
        configure_logging(app)

    db.init_app(app)

    if app.config.get("RAVEN_ENABLE_LDAP"):
        raven_logger.info("Enabling LDAP")
        ldap_manager.init_app(app)

    try:
        fsqla.FsModels.set_db_info(db)
    except sqlalchemy.exc.InvalidRequestError:
        pass

    from raven.models import import_all_models
    from raven.models.role import Role
    from raven.models.SupportingDocument import SupportingDocument  # noqa: F401
    from raven.models.user import User
    from raven.no_email import NoEmailMailUtil, apply_no_email_settings
    from raven.PasswordValidator import PasswordValidator

    # Every model a relationship names by string, so mapper configuration succeeds here
    import_all_models()

    flask_wtf.CSRFProtect(app)
    apply_no_email_settings(app.config)
    app.security = Security(
        app,
        SQLAlchemyUserDatastore(db, User, Role),
        mail_util_cls=NoEmailMailUtil,
        password_util_cls=PasswordValidator,
    )

    @app.route("/status")
    def status():
        return {"status": "ok"}

    return app


def authenticate_eud_credentials(app, username, password):
    """The User for a streaming <auth> username/password, or None. Call inside an app context.

    The password may also be the enrollment token ATAK keeps as its password: accepted without
    counting a use or applying max uses (expiry, disabled and subject are still checked)."""
    if not username or not password:
        return None
    try:
        if app.config.get("RAVEN_ENABLE_LDAP"):
            result = ldap_manager.authenticate(username, password)
            if getattr(result, "status", None) is None or result.status.name != "success":
                return None
            from raven.blueprints.raven_api.ldap_api import save_user

            return save_user(result.user_dn, result.user_id, result.user_info, result.user_groups)

        from flask_security import verify_password

        from raven.models.Token import Token

        user = app.security.datastore.find_user(username=username)
        if user is None:
            return None
        if user.password and verify_password(password, user.password):
            return user
        if Token.verify_token(password, user.username, consume=False, check_max=False):
            return user
        return None
    except Exception as e:
        raven_logger.error(f"Failed to authenticate {username}: {e}")
        return None


def _naive_utc(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _parse_iso8601(value):
    if not value:
        return None
    try:
        return _naive_utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _phone_value(phone):
    """euds.phone_number is a BIGINT: digits only, or NULL."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if not digits or len(digits) > 18:
        return None
    return int(digits)


def _take_messages(buffer):
    """Splits off every complete <event>/<auth> message. Returns (messages, rest) where rest is
    the incomplete tail, kept for the next read."""
    messages = []
    while True:
        found = [(buffer.find(tag), tag) for tag in _CLOSING_TAGS]
        found = [(i, tag) for i, tag in found if i >= 0]
        if not found:
            return messages, buffer
        i, tag = min(found)
        end = i + len(tag)
        messages.append((buffer[:end], tag))
        buffer = buffer[end:]


class EudHandler(socketserver.BaseRequestHandler):
    # Every attribute has a class-level default so a handler is usable before setup() runs
    # (tests build handlers with __new__). The mutable ones are replaced per connection.

    # socketserver plumbing and the Flask app
    request = None
    client_address = None
    timeout = 1.0
    app = None
    logger = raven_logger
    send_lock = threading.RLock()

    # TLS / authentication
    is_ssl = False
    is_authenticated = False
    common_name = None
    cert_serial = None
    user = None
    conn_token = None
    cert_control_bound = False

    # Who is on the other end
    eud = None
    uid = None
    callsign = None
    platform = None
    phone_number = None
    group_memberships = []

    # RabbitMQ consumer state
    rabbit_connection = None
    rabbit_channel = None
    iothread = None
    is_consuming = False
    bound_queues = []
    consumer_tags = []
    cached_messages = []
    callsign_queue_refused = False

    # Lifecycle and duplicate-connection bookkeeping
    shutdown = False
    superseded = False
    refused_uids = frozenset()
    handed_over_uids = frozenset()
    offline_chat_delivered = False
    last_rx_write = 0.0

    # Private state, also with safe defaults
    _closed = False
    _queues_set_up = False
    _state_lock = threading.RLock()

    # ------------------------------------------------------------------ helpers

    def _client_ip(self):
        address = self.client_address
        if isinstance(address, (tuple, list)) and address:
            return address[0]
        return address

    def _ident(self):
        return self.callsign or self.uid or self._client_ip()

    @property
    def is_udp(self):
        return isinstance(self.request, tuple)

    def _own_list(self, name):
        """This instance's own copy of a list attribute (the class default must never change)."""
        if name not in self.__dict__:
            setattr(self, name, list(getattr(self, name)))
        return self.__dict__[name]

    @contextlib.contextmanager
    def _in_app(self):
        """An app context, unless one is already active (a nested one would get its own session)."""
        if has_app_context():
            yield
        else:
            with self.app.app_context():
                yield

    def _ttl(self):
        try:
            return str(self.app.config.get("RAVEN_RABBITMQ_TTL"))
        except AttributeError:
            return None

    def _run_rabbit(self, fn):
        """Runs fn on the pika ioloop thread when that is running (pika isn't thread safe),
        otherwise right here. Errors are logged, never raised."""

        def safe():
            try:
                fn()
            except Exception as e:
                self.logger.error(f"RabbitMQ operation failed for {self._ident()}: {e}")
                self.logger.debug(traceback.format_exc())

        connection = self.rabbit_connection
        thread = self.iothread
        if (
            connection is not None
            and thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
            and not getattr(connection, "is_closed", True)
        ):
            try:
                connection.ioloop.add_callback_threadsafe(safe)
                return
            except Exception as e:
                self.logger.debug(f"Couldn't schedule on the RabbitMQ ioloop: {e}")
        safe()

    def _publish(self, exchange, routing_key, body, expiration=None):
        channel = self.rabbit_channel

        def do():
            if channel is None or not channel.is_open:
                return
            channel.basic_publish(
                exchange=exchange,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(expiration=expiration),
            )

        self._run_rabbit(do)

    def _channel_open(self):
        channel = self.rabbit_channel
        return channel is not None and channel.is_open is True

    # ------------------------------------------------------------------ set-up

    def create_app(self):
        return build_flask_app(__name__)

    def setup(self):
        if not isinstance(self.request, tuple):
            metrics.inc("raven_eud_connections_accepted_total")
            try:
                sock = self.request
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 15)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 30000)
            except OSError as e:
                self.logger.debug(f"Couldn't set TCP keepalive: {e}")

        self.conn_token = uuid.uuid4().hex
        self.superseded = False
        self.consumer_tags = []
        self.cached_messages = []
        self.bound_queues = []
        self.group_memberships = []
        self.send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._channel_ready = threading.Event()

        self.app = self.create_app()

        try:
            config = self.app.config
            parameters = pika.ConnectionParameters(
                host=config.get("RAVEN_RABBITMQ_SERVER_ADDRESS"),
                credentials=pika.PlainCredentials(
                    config.get("RAVEN_RABBITMQ_USERNAME"), config.get("RAVEN_RABBITMQ_PASSWORD")
                ),
            )
            self.rabbit_connection = pika.SelectConnection(
                parameters,
                on_open_callback=self.on_connection_open,
                on_open_error_callback=self.on_connection_open_error,
                on_close_callback=self.on_close,
            )
            self.iothread = threading.Thread(
                target=self.rabbit_connection.ioloop.start, name=f"IOLOOP_{self.common_name}", daemon=True
            )
            self.iothread.start()
        except Exception as e:
            self.logger.error(f"Failed to connect to RabbitMQ for {self._client_ip()}: {e}")
            return

    def on_connection_open(self, connection):
        if self._closed:
            # The client left while the broker link was still opening (a scanner dropping the TLS
            # handshake, say): close it now it is open; closing it mid-open crashes pika's ioloop
            connection.close()
            return
        connection.channel(on_open_callback=self.on_channel_open)

    def on_connection_open_error(self, connection, error):
        if self._closed:
            self.logger.debug(f"RabbitMQ link for {self._ident()} not opened, client already gone: {error}")
        else:
            self.logger.error(f"Failed to connect to RabbitMQ for {self._ident()}: {error}")
        try:
            connection.ioloop.stop()
        except Exception:
            pass

    def on_channel_open(self, channel):
        self.rabbit_channel = channel
        channel.add_on_close_callback(self.on_channel_close)
        for name, kind, durable in _DECLARED_EXCHANGES:
            channel.exchange_declare(exchange=name, exchange_type=kind, durable=durable)

        self.bind_cert_control_queue()

        cached = list(self.cached_messages)
        self.cached_messages = []
        for event in cached:
            self.publish_cot(event)

        # A device that identified itself before the channel was open gets its queues now
        self._setup_queues_once()

        if self.eud is not None:
            self._publish_eud_update([])

        ready = getattr(self, "_channel_ready", None)
        if ready is not None:
            ready.set()

    # ------------------------------------------------------------------ framing

    def handle(self):
        if self.is_udp:
            self._handle_datagram()
            return

        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        while not self.shutdown:
            try:
                data = self.request.recv(65536)
            except Exception as e:
                self.logger.debug(f"recv from {self._client_ip()} ended: {e}")
                break
            if not data:
                self.logger.debug(f"{self._client_ip()} closed the connection")
                break
            metrics.inc("raven_bytes_received_total", amount=len(data))
            # Incremental: a UTF-8 character split across two reads is completed by the next one
            pending += decoder.decode(data)

            messages, pending = _take_messages(pending)
            self._process_messages(messages)

            if len(pending) > MAX_PENDING_COT_CHARS:
                who = self.uid or self.common_name or self._client_ip()
                self.logger.warning(f"Dropping {who}: {len(pending)} characters buffered without a complete event")
                metrics.inc("raven_cot_dropped_total", metrics.DROP_PARSE_ERROR)
                break

        self.close_connection()

    def _process_messages(self, messages):
        for text, tag in messages:
            start = text.find("<event") if tag == "</event>" else text.find("<auth>")
            if start < 0:
                continue  # trailing data or a stray closing tag
            message = text[start:]
            try:
                etree.fromstring(message.encode(), _XML_CHECK_PARSER)
            except etree.XMLSyntaxError as e:
                self.logger.error(f"Failed to parse: {e}")
                metrics.inc("raven_cot_dropped_total", metrics.DROP_PARSE_ERROR)
                return
            if tag == "</event>":
                self.handle_cot(message)
            else:
                self.handle_auth(message)

    def _handle_datagram(self):
        """A UDP datagram: one or more complete CoT events, published anonymously (uid and user_id
        null). No session, no device registration, nothing sent back."""
        data = self.request[0]
        metrics.inc("raven_bytes_received_total", amount=len(data))
        # Give the broker a moment so the events are published rather than cached
        ready = getattr(self, "_channel_ready", None)
        if ready is not None and self.rabbit_connection is not None:
            ready.wait(5)
        messages, _rest = _take_messages(data.decode("utf-8", errors="replace"))
        self._process_messages([(text, tag) for text, tag in messages if tag == "</event>"])
        if self.cached_messages:
            self.logger.warning(f"RabbitMQ wasn't ready: dropped a datagram from {self._client_ip()}")
        self.shutdown = True

    def finish(self):
        try:
            self.close_connection()
        except Exception as e:
            self.logger.debug(f"Error closing {self._client_ip()}: {e}")
        self._wait_for_ioloop()

    # ------------------------------------------------------------------ auth

    def handle_auth(self, auth: str):
        self.logger.debug(
            f"Auth: {_PASSWORD_RE.sub(lambda m: m.group(1) + m.group(2) + '***' + m.group(2), auth or '')}"
        )
        if not self.is_ssl or self.is_authenticated or not (auth or self.common_name):
            return

        with self._in_app():
            uid = None
            if auth:
                cot = BeautifulSoup(auth, "xml").find("cot")
                attrs = cot.attrs if cot is not None else {}
                username = attrs.get("username")
                uid = attrs.get("uid")
                user = authenticate_eud_credentials(self.app, username, attrs.get("password"))
                if user is None:
                    self.logger.warning(f"Failed login for user {username}")
                    return self._refuse_login()
                if self.common_name and self.common_name.lower() != (user.username or "").lower():
                    self.logger.warning(f"Certificate {self.common_name} can't log in as {user.username}: refused")
                    return self._refuse_login()
            else:
                user = self.app.security.datastore.find_user(username=self.common_name)
                if user is None:
                    self.logger.warning(f"User {self.common_name} does not exist")
                    return self._refuse_login()

            if not user.active:
                self.logger.warning(f"User {user.username} is deactivated")
                return self._refuse_login()

            if auth:
                self.logger.info(f"Successful login from {user.username}")
            else:
                self.logger.info(f"{user.username} is ID'ed by cert")
            self.is_authenticated = True
            self.user = user

            if uid:
                self._claim_uid(uid, user)

    def _refuse_login(self):
        metrics.inc("raven_eud_auth_failures_total")
        self.close_connection()

    def _claim_uid(self, uid, user):
        from raven.models.EUD import EUD

        if self.uid_owned_by_other_user(uid, user):
            self.refuse_uid(uid, user, "<auth>")
        else:
            eud, created = self.get_or_create_eud(uid)
            eud.user_id = user.id
            if created and self.callsign:
                eud.callsign = self.callsign
            try:
                db.session.commit()
            except sqlalchemy.exc.IntegrityError:
                db.session.rollback()
                self.logger.warning(
                    f"Callsign {self.callsign} is used by another device; only linking {uid} to {user.username}"
                )
                db.session.execute(update(EUD).where(EUD.uid == uid).values(user_id=user.id))
                db.session.commit()
        # Its attributes are read after this app context ends
        db.session.refresh(user)

    # ------------------------------------------------------------------ uid ownership

    def other_owner_of_uid(self, uid, user):
        """(the other active user who owns uid, whether that device is Connected) or (None, False)."""
        from raven.models.EUD import EUD

        with self._in_app():
            eud = db.session.execute(select(EUD).where(EUD.uid == uid)).scalar_one_or_none()
            if eud is None or eud.user_id is None:
                return None, False
            if user is not None and eud.user_id == user.id:
                return None, False
            owner = db.session.get(self.app.security.datastore.user_model, eud.user_id)
            if owner is None or not owner.active:
                return None, False
            return owner, eud.last_status == "Connected"

    def uid_owned_by_other_user(self, uid, user) -> bool:
        owner, connected = self.other_owner_of_uid(uid, user)
        if owner is None:
            return False
        if connected:
            return True
        self.note_handover(uid, owner, user)
        return False

    def note_handover(self, uid, previous_owner, user):
        if uid in self.handed_over_uids:
            return
        self.handed_over_uids = self.handed_over_uids | {uid}
        previous = getattr(previous_owner, "username", previous_owner)
        new = getattr(user, "username", user)
        self.logger.info(f"Device {uid} was registered to {previous} and not connected: now belongs to {new}")
        try:
            from raven.audit import record_audit_event

            with self._in_app():
                record_audit_event(
                    "eud",
                    "uid_handover",
                    username=new,
                    success=True,
                    ip=self._client_ip(),
                    details=f"Device uid {uid} was registered to {previous} and not connected; it now belongs to {new}",
                )
        except Exception as e:
            self.logger.error(f"Failed to audit the handover of {uid}: {e}")

    def refuse_uid(self, uid, user, via):
        if uid in self.refused_uids:
            return
        self.refused_uids = self.refused_uids | {uid}
        username = getattr(user, "username", user)
        self.logger.warning(
            f"{username} tried to use device uid {uid} ({via}), which another user's connected device has"
        )
        try:
            from raven.audit import record_audit_event

            with self._in_app():
                record_audit_event(
                    "eud",
                    "uid_takeover_refused",
                    username=username,
                    success=False,
                    ip=self._client_ip(),
                    details=(
                        f"Connection as {username} tried to use device uid {uid} ({via}), which is "
                        "registered to another user; its events were dropped. An admin can move the "
                        "device to this user with Assign EUD."
                    ),
                )
        except Exception as e:
            self.logger.error(f"Failed to audit the refused uid {uid}: {e}")

    def callsign_owned_by_other_user(self, callsign, uid, user) -> bool:
        from raven.models.EUD import EUD

        with self._in_app():
            rows = db.session.execute(
                select(EUD).where(
                    EUD.callsign == callsign,
                    EUD.uid != uid,
                    EUD.last_status == "Connected",
                    EUD.user_id.isnot(None),
                    EUD.user_id != user.id,
                )
            ).scalars()
            user_model = self.app.security.datastore.user_model
            for row in rows:
                owner = db.session.get(user_model, row.user_id)
                if owner is not None and owner.active:
                    return True
        return False

    # ------------------------------------------------------------------ receiving

    def pong(self, event) -> bool:
        """Answers a t-x-c-t ping with the ping itself (what ATAK accepts). True if answered."""
        if event is None or event.attrs.get("type") != "t-x-c-t":
            return False
        if self.is_udp:
            return True  # never answer a spoofable source address; just drop the ping
        try:
            self._send(str(event).encode())
            return True
        except Exception as e:
            self.logger.error(f"Failed to answer a ping from {self._ident()}: {e}")
            return False

    def handle_cot(self, cot: str):
        self.logger.debug(f"CoT from {self._ident()}: {cot}")
        event = BeautifulSoup(cot, "xml").find("event")
        if event is None:
            return

        if self.is_ssl and not self.is_authenticated:
            self.logger.warning(f"{self._client_ip()} isn't authenticated, dropping its CoT")
            metrics.inc("raven_cot_dropped_total", metrics.DROP_UNAUTHENTICATED)
            return

        if self.uid:
            self.note_rx()

        if self.pong(event):
            return

        metrics.inc("raven_cot_received_total", metrics.CATEGORY_LABELS[metrics.cot_category(event.attrs.get("type"))])

        if event.attrs.get("uid") in self.refused_uids:
            metrics.inc("raven_cot_dropped_total", metrics.DROP_UNAUTHENTICATED)
            return

        if not self.uid:
            accepted = self.parse_device_info(event)
            try:
                with self._in_app():
                    db.session.close()
                    db.engine.dispose()
            except Exception as e:
                self.logger.debug(f"Couldn't release the DB session: {e}")
            if accepted is False:
                metrics.inc("raven_cot_dropped_total", metrics.DROP_UNAUTHENTICATED)
                return
            if self.refused_uids and not self.uid:
                # Its chat and markers must not go out under their own uids
                metrics.inc("raven_cot_dropped_total", metrics.DROP_UNAUTHENTICATED)
                return

        self.publish_cot(event)

    def publish_cot(self, event):
        if event is None or event == "":
            return
        if not self._channel_open():
            if self.shutdown:
                self.logger.debug(f"Dropping a CoT from {self._ident()}: connection closing")
                return
            self._own_list("cached_messages").append(event)
            self.logger.warning(f"RabbitMQ channel for {self._ident()} is closed, caching the CoT")
            return

        cot = str(event)
        user_id = self.user.id if self.user is not None else None
        ttl = self._ttl()
        self._publish("firehose", "", json.dumps({"uid": self.uid, "cot": cot}), ttl)
        self._publish("cot_parser", "cot_parser", json.dumps({"uid": self.uid, "cot": cot, "user_id": user_id}), ttl)

    # ------------------------------------------------------------------ registration

    def parse_device_info(self, event):
        """Registers the device the event describes. None: not an identifying event (publish it
        normally); False: refused (drop it); True: identified."""
        from raven.models.EUD import EUD

        # UDP datagrams are unauthenticated and their source is spoofable: they never create or
        # change an EUD row (ownership, status, callsign, team), only get published anonymously
        if self.is_udp:
            return None

        takv = event.find("takv")
        contact = event.find("contact")
        if takv is None and contact is None:
            return None

        uid = event.attrs.get("uid")
        if contact is None or not uid or uid.endswith("ping"):
            return None
        if self.is_ssl and self.user is None:
            return None
        if uid in self.refused_uids:
            return False

        takv_attrs = takv.attrs if takv is not None else {}
        platform = takv_attrs.get("platform")
        relay = platform in RELAY_PLATFORMS

        keep_owner = False
        if relay:
            # A relay never takes a radio's registration, connected owner or not
            owner, _connected = self.other_owner_of_uid(uid, self.user)
            keep_owner = owner is not None
        elif self.user is not None and self.uid_owned_by_other_user(uid, self.user):
            self.refuse_uid(uid, self.user, "contact")
            return False

        self.uid = uid
        self.platform = platform
        device = takv_attrs.get("device")
        os_name = takv_attrs.get("os")
        version = takv_attrs.get("version")

        callsign = contact.attrs.get("callsign")
        if callsign:
            self.callsign = callsign
            if self.user is not None and self.callsign_owned_by_other_user(callsign, uid, self.user):
                self.callsign_queue_refused = True
                self.logger.warning(
                    f"Callsign {callsign} belongs to another user's connected device: {uid} gets no callsign queue"
                )

        self._setup_queues_once()

        phone = contact.attrs.get("phone")
        if phone:
            self.phone_number = phone

        with self._in_app():
            group = event.find("__group")
            team = None
            role = None
            if group is not None and group.attrs.get("name"):
                team = self.get_or_create_team(bleach.clean(group.attrs.get("name")))
                role = group.attrs.get("role")

            eud, _created = self.get_or_create_eud(uid)
            values = {
                "os": os_name,
                "platform": platform,
                "version": version,
                "phone_number": _phone_value(self.phone_number),
                "last_event_time": _parse_iso8601(event.attrs.get("start")),
                "federated_from": None,
                "last_status": "Connected",
            }
            if device:
                values["device"] = device
            if not keep_owner:
                values["user_id"] = self.user.id if self.user is not None else None
            if eud.meshtastic_id is None:
                values["meshtastic_id"] = self._meshtastic_id(platform, takv_attrs.get("meshtastic_id"))
            values["meshtastic_macaddr"] = takv_attrs.get("macaddr") or base64.b64encode(os.urandom(6)).decode()
            if team is not None:
                values["team_id"] = team.id
                values["team_role"] = bleach.clean(role) if role else None

            for key, value in values.items():
                setattr(eud, key, value)
            if self.callsign:
                eud.callsign = self.callsign
            try:
                db.session.commit()
            except sqlalchemy.exc.IntegrityError:
                db.session.rollback()
                self.logger.warning(f"Callsign {self.callsign} is used by another device; updating {uid} without it")
                db.session.execute(update(EUD).where(EUD.uid == uid).values(**values))
                db.session.commit()
                eud = db.session.execute(select(EUD).where(EUD.uid == uid)).scalar_one()

            self._deliver_clear_commands(uid)
            self.deliver_offline_chat(uid, platform)

            self.eud = eud
            if self.rabbit_channel is not None:
                self._publish_eud_update(None)
        return True

    @staticmethod
    def _meshtastic_id(platform, takv_value):
        if platform == "Meshtastic":
            try:
                return int(str(takv_value), 16)
            except (TypeError, ValueError):
                pass
        return random.getrandbits(32)

    def _deliver_clear_commands(self, uid):
        """Writes a queued remote-clear command straight to the socket. Never raises."""
        try:
            from raven.models.PendingClearCommand import PendingClearCommand

            rows = (
                db.session.execute(
                    select(PendingClearCommand).where(
                        PendingClearCommand.eud_uid == uid, PendingClearCommand.delivered_at.is_(None)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return
            clearmaps = any(bool(row.clearmaps) for row in rows)
            cot = remote_clear.build_clear_cot(uid, clearmaps=clearmaps, node_id=self.app.config.get("RAVEN_NODE_ID"))
            self._send(cot.encode())
            now = _utcnow()
            for row in rows:
                row.delivered_at = now
            db.session.commit()
            self.logger.warning(
                f"Sent the queued remote clear to {uid} ({len(rows)} request(s), clearmaps={clearmaps})"
            )
        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            self.logger.error(f"Failed to deliver the queued remote clear to {uid}: {e}")

    @staticmethod
    def get_or_create_team(name):
        from raven.db_upsert import insert_ignore
        from raven.models.Chatrooms import Chatroom
        from raven.models.Team import Team

        chatroom = db.session.execute(select(Chatroom).where(Chatroom.name == name)).scalars().first()
        values = {"name": name}
        if chatroom is not None:
            values["chatroom_id"] = chatroom.id
        insert_ignore(db.session, Team, values, ["name"])
        db.session.commit()
        team = db.session.execute(select(Team).where(Team.name == name)).scalar_one()
        if team.chatroom_id is None and chatroom is not None:
            team.chatroom_id = chatroom.id
            db.session.commit()
        return team

    @staticmethod
    def get_or_create_eud(uid):
        from raven.db_upsert import insert_ignore
        from raven.models.EUD import EUD

        created = insert_ignore(db.session, EUD, {"uid": uid}, ["uid"])
        db.session.commit()
        eud = db.session.execute(select(EUD).where(EUD.uid == uid)).scalar_one()
        return eud, created

    # ------------------------------------------------------------------ queues

    def _setup_queues_once(self):
        """Declares, binds and consumes the device's queues, once, as soon as both its uid and an
        open channel exist (whichever comes last calls this successfully)."""

        def do():
            with self._state_lock:
                if self._queues_set_up or not self.uid or not self.callsign or not self._channel_open():
                    return
                if self.platform in RELAY_PLATFORMS:
                    return
                self._queues_set_up = True
            self._setup_queues()

        if self.rabbit_connection is None:
            do()
        else:
            self._run_rabbit(do)

    def _bind(self, exchange, queue, routing_key):
        entry = {"exchange": exchange, "routing_key": routing_key, "queue": queue}
        self.rabbit_channel.queue_bind(exchange=exchange, queue=queue, routing_key=routing_key)
        bound = self._own_list("bound_queues")
        if entry not in bound:
            bound.append(entry)

    def _out_group_keys(self):
        if not self.is_ssl or self.user is None:
            return ["__ANON__.OUT"]
        from raven.models.GroupUser import GroupUser

        with self._in_app():
            rows = (
                db.session.execute(
                    select(GroupUser).where(GroupUser.user_id == self.user.id, GroupUser.direction == "OUT")
                )
                .scalars()
                .all()
            )
            if not rows:
                return ["__ANON__.OUT"]
            keys = []
            memberships = self._own_list("group_memberships")
            for row in rows:
                memberships.append(row)
                if row.enabled:
                    keys.append(f"{row.group.name}.OUT")
            return keys

    def _setup_queues(self):
        channel = self.rabbit_channel
        uid = self.uid
        callsign = self.callsign
        with_callsign = not self.callsign_queue_refused

        if with_callsign:
            channel.queue_declare(queue=callsign)
        channel.queue_declare(queue=uid)

        for key in self._out_group_keys():
            self._bind("groups", uid, key)
        self._bind("missions", uid, "missions")
        self._bind("dms", uid, uid)
        if with_callsign:
            self._bind("dms", callsign, callsign)

        # The offline backlog reaches the device ahead of live traffic
        self.deliver_offline_chat(uid, self.platform)

        tags = self._own_list("consumer_tags")
        if with_callsign:
            tags.append(channel.basic_consume(queue=callsign, on_message_callback=self.on_message, auto_ack=True))
        tags.append(channel.basic_consume(queue=uid, on_message_callback=self.on_message, auto_ack=True))
        self.is_consuming = True

        # Supersede an older connection of the same device
        control = f"ctl.{uid}.{self.conn_token}"
        channel.queue_declare(queue=control, exclusive=True, auto_delete=True)
        channel.queue_bind(exchange="eud_control", queue=control, routing_key=uid)
        channel.basic_consume(queue=control, on_message_callback=self.on_control_message, auto_ack=True)
        channel.basic_publish(
            exchange="eud_control",
            routing_key=uid,
            body=json.dumps({"action": "supersede", "uid": uid, "except": self.conn_token}),
            properties=pika.BasicProperties(expiration="10000"),
        )

    def unbind_rabbitmq_queues(self):
        channel = self.rabbit_channel
        if not self.uid or channel is None or channel.is_closing or channel.is_closed:
            return
        for entry in list(self.bound_queues):
            try:
                channel.queue_unbind(queue=entry["queue"], exchange=entry["exchange"], routing_key=entry["routing_key"])
            except Exception as e:
                self.logger.debug(f"Unbinding {entry} failed: {e}")

    def request_cert_control_queue(self):
        connection = self.rabbit_connection
        if connection is None or connection.is_closed:
            return
        try:
            connection.ioloop.add_callback_threadsafe(self.bind_cert_control_queue)
        except Exception as e:
            self.logger.error(f"Failed to schedule the certificate control queue for {self._client_ip()}: {e}")

    def bind_cert_control_queue(self):
        """Runs on the ioloop: a private queue receiving revoke messages for this certificate."""
        try:
            if self.cert_control_bound or not self.cert_serial or not self._channel_open():
                return
            self.cert_control_bound = True
            channel = self.rabbit_channel
            queue = f"ctl.cert.{self.cert_serial}.{self.conn_token}"
            channel.queue_declare(queue=queue, exclusive=True, auto_delete=True)
            channel.queue_bind(
                exchange="eud_control",
                queue=queue,
                routing_key=cert_revocation.CERT_ROUTING_PREFIX + self.cert_serial,
            )
            channel.basic_consume(queue=queue, on_message_callback=self.on_control_message, auto_ack=True)
            # Revoked between the handshake check and now
            if cert_revocation.is_revoked(self.app.config.get("RAVEN_CA_FOLDER"), self.cert_serial):
                self.logger.warning(f"Certificate {self.cert_serial} of {self._client_ip()} was revoked, disconnecting")
                self.close_connection()
        except Exception as e:
            self.logger.error(f"Failed to bind the certificate control queue for {self._client_ip()}: {e}")
            self.logger.error(traceback.format_exc())

    # ------------------------------------------------------------------ delivery

    def _send(self, data: bytes):
        if self.is_udp:
            raise OSError("nothing is ever sent back over UDP")
        with self.send_lock:
            self.request.sendall(data)

    def on_message(self, ch, deliver, props, body):
        try:
            message = json.loads(body)
            if message.get("kick"):
                self.logger.warning(f"{self.callsign} was disconnected by admin (account deactivated)")
                self.close_connection()
                return
            if message["uid"] != self.uid:
                cot = message["cot"]
                data = cot.encode("utf-8")
                self._send(data)
                metrics.inc("raven_bytes_sent_total", amount=len(data))
                metrics.inc("raven_cot_sent_total", metrics.CATEGORY_LABELS[metrics.cot_category_from_xml(cot)])
        except Exception as e:
            metrics.inc("raven_cot_dropped_total", metrics.DROP_SEND_ERROR)
            self.logger.error(f"Failed to send a message to {self._ident()}: {e}")
            self.close_connection()
            self.logger.error(traceback.format_exc())

    def _cancel_consumers(self):
        channel = self.rabbit_channel
        for tag in list(self.consumer_tags):
            try:
                channel.basic_cancel(tag)
            except Exception as e:
                self.logger.debug(f"Cancelling consumer {tag} failed: {e}")
        self.is_consuming = False

    def on_control_message(self, ch, method, props, body):
        try:
            message = json.loads(body)
            action = message.get("action")
            if action == "revoke":
                if self.cert_serial and cert_revocation.normalize_serial(message.get("serial")) == self.cert_serial:
                    self.logger.warning(f"Certificate {self.cert_serial} was revoked, disconnecting {self._ident()}")
                    self.close_connection()
                return
            if message.get("uid") != self.uid:
                return
            if action == "supersede":
                if message.get("except") == self.conn_token:
                    return
                self.superseded = True
                self._cancel_consumers()
                self.logger.warning(f"{self._ident()} connected again: this older connection stops receiving")
            elif action == "disconnect":
                self.logger.warning(f"{self._ident()} disconnected by the server")
                self.superseded = True
                self._cancel_consumers()
                try:
                    self.request.shutdown(socket.SHUT_RDWR)
                except OSError as e:
                    self.logger.debug(f"Socket shutdown failed: {e}")
        except Exception as e:
            self.logger.error(f"Failed to handle a control message for {self._ident()}: {e}")
            self.logger.error(traceback.format_exc())

    def deliver_offline_chat(self, uid, platform):
        with self._state_lock:
            if self.offline_chat_delivered or platform in offline_chat.NO_QUEUE_PLATFORMS:
                return
            self.offline_chat_delivered = True
        try:
            with self._in_app():
                try:
                    user_id = self.user.id if self.user is not None else None
                    count = offline_chat.deliver_pending(db.session, uid, self._send, user_id=user_id)
                    if count:
                        self.logger.info(f"Delivered {count} offline chat message(s) to {uid}")
                except Exception:
                    db.session.rollback()
                    raise
        except Exception as e:
            self.logger.error(f"Failed to deliver offline chat to {uid}: {e}")

    def _publish_eud_update(self, skip_sid):
        """The web map's EUD update (Flask-SocketIO message queue format)."""
        if not self._channel_open() or not self.uid:
            return
        try:
            from raven.models.EUD import EUD

            with self._in_app():
                eud = db.session.execute(select(EUD).where(EUD.uid == self.uid)).scalar_one_or_none()
                if eud is None:
                    return
                data = eud.to_json()
            body = json.dumps(
                {
                    "method": "emit",
                    "event": "eud",
                    "data": data,
                    "namespace": "/socket.io",
                    "room": None,
                    "skip_sid": skip_sid,
                    "callback": None,
                    "binary": False,
                    "host_id": uuid.uuid4().hex,
                },
                default=str,
            )
            self._publish("flask-socketio", "", body, self._ttl())
        except Exception as e:
            self.logger.error(f"Failed to publish the map update for {self.uid}: {e}")

    # ------------------------------------------------------------------ last received

    def note_rx(self):
        try:
            now = time.monotonic()
            if self.last_rx_write and now - self.last_rx_write < offline_chat.LAST_RX_WRITE_INTERVAL:
                return
            self.last_rx_write = now
            with self.app.app_context():
                try:
                    offline_chat.record_rx(db.session, self.uid)
                    self.restore_connected_status()
                finally:
                    db.session.close()
                    db.engine.dispose()
        except Exception as e:
            self.logger.debug(f"Couldn't record last_rx_at for {self.uid}: {e}")

    def restore_connected_status(self):
        """Puts back Connected for a device another process' restart marked Disconnected."""
        if self.superseded or not self.uid:
            return
        from raven.models.EUD import EUD

        with self._in_app():
            eud = db.session.execute(select(EUD).where(EUD.uid == self.uid)).scalar_one_or_none()
            if eud is None or eud.last_status == "Connected":
                return
            eud.last_status = "Connected"
            eud.last_event_time = _utcnow()
            db.session.commit()
            self.logger.info(f"{self.uid} is still connected, status restored")
            self._publish_eud_update(None)

    # ------------------------------------------------------------------ disconnect

    def _close_channel(self):
        channel = self.rabbit_channel
        if channel is not None and not channel.is_closing and not channel.is_closed:
            channel.close()
            return
        connection = self.rabbit_connection
        # Only an open link is closed here; one still opening is closed by on_connection_open
        if connection is not None and connection.is_open:
            connection.close()

    def _publish_disconnect(self, closed_at=None):
        """The disconnect notice for cot_parser. "time" (and the AMQP timestamp) say when the
        connection closed, so cot_parser can tell a notice that arrives after the device has
        already reconnected."""
        channel = self.rabbit_channel
        if channel is None or not channel.is_open:
            return
        closed_at = closed_at or datetime.now(timezone.utc)
        body = json.dumps(
            {
                "uid": self.uid,
                "cot": None,
                "disconnected": True,
                "user_id": self.user.id if self.user is not None else None,
                "time": iso8601_string_from_datetime(closed_at),
            }
        )
        channel.basic_publish(
            exchange="cot_parser",
            routing_key="cot_parser",
            body=body,
            properties=pika.BasicProperties(expiration=self._ttl(), timestamp=int(closed_at.timestamp())),
        )

    def _mark_disconnected(self):
        from raven.models.EUD import EUD

        try:
            with self._in_app():
                db.session.execute(
                    update(EUD)
                    .where(EUD.uid == self.uid)
                    .values(last_status="Disconnected")
                    .execution_options(synchronize_session=False)
                )
                db.session.commit()
        except Exception as e:
            self.logger.debug(f"Couldn't mark {self.uid} Disconnected: {e}")

    def _wait_for_ioloop(self):
        thread = self.iothread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(5)

    def close_connection(self):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True

        self.logger.info(f"{self._client_ip()} disconnected")

        if self.superseded:
            self.logger.info(f"{self._ident()} was superseded: closing without touching its queues or status")
            self._run_rabbit(self._close_channel)
            self.shutdown = True
            return

        if not self.is_udp:
            closed_at = datetime.now(timezone.utc)
            self._run_rabbit(lambda: self._publish_disconnect(closed_at))
            self._run_rabbit(self.unbind_rabbitmq_queues)
            if self.uid:
                self._mark_disconnected()
        self._run_rabbit(self._close_channel)
        self.shutdown = True

        if self.is_udp or self.request is None:
            return  # the UDP socket is the server's
        try:
            self.request.shutdown(socket.SHUT_RDWR)
        except OSError as e:
            self.logger.debug(f"Socket shutdown for {self._client_ip()}: {e}")
        finally:
            try:
                self.request.close()
            except OSError as e:
                self.logger.debug(f"Socket close for {self._client_ip()}: {e}")

    def on_channel_close(self, channel, error):
        who = self._ident()
        if isinstance(error, (pika.exceptions.ChannelClosedByClient, pika.exceptions.ConnectionClosedByClient)):
            self.logger.debug(f"RabbitMQ channel for {who} closed: {error}")
        else:
            self.logger.error(f"RabbitMQ channel for {who} closed: {error}")
        connection = self.rabbit_connection
        # Only an open link is closed here; one still opening is closed by on_connection_open
        if connection is not None and connection.is_open:
            connection.close()
        self.shutdown = True

    def on_close(self, connection, error):
        try:
            connection.ioloop.add_callback_threadsafe(connection.ioloop.stop)
        except Exception:
            pass
        self.logger.info(f"Connection closed for {self._client_ip()}: {error}")

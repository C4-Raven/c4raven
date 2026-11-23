"""Built-in defaults for every Flask config key.

Values are computed once, when this module is imported, and many of them read an environment
variable first. On the first start every upper-case attribute except RAVEN_LANGUAGES is written to
RAVEN_DATA_FOLDER/config.yml (raven.config_file.write_default_config), which overrides these
defaults from then on.

Tests monkeypatch the class attributes directly, so they must stay plain class attributes.
"""

import os
import secrets
import string
from pathlib import Path

import pyotp


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("true", "1", "yes")


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _env_list(name: str, default: list) -> list:
    value = os.getenv(name)
    if value is None:
        return list(default)
    return value.split(",")


def _scheduled_job(name: str, trigger: str, paused: bool = False, **schedule) -> dict:
    """A Flask-APScheduler job entry running raven.blueprints.scheduled_jobs.<name>.

    Paused jobs carry "next_run_time": None; enabled ones must not have the key at all.
    """
    job = {"id": name, "func": f"raven.blueprints.scheduled_jobs:{name}", "trigger": trigger, **schedule}
    if paused:
        job["next_run_time"] = None
    return job


_DATA_FOLDER = os.getenv("RAVEN_DATA_FOLDER", os.path.join(Path.home(), "raven"))

_CA_COUNTRY = os.getenv("RAVEN_CA_COUNTRY", "WW")
_CA_STATE = os.getenv("RAVEN_CA_STATE", "XX")
_CA_CITY = os.getenv("RAVEN_CA_CITY", "YY")
_CA_ORGANIZATION = os.getenv("RAVEN_CA_ORGANIZATION", "ZZ")
_CA_ORGANIZATIONAL_UNIT = os.getenv("RAVEN_CA_ORGANIZATIONAL_UNIT", "Raven")

_UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", os.path.join(_DATA_FOLDER, "uploads"))
os.makedirs(_UPLOAD_FOLDER, exist_ok=True)

_FEDHUB_FILES = "/opt/tak/federation-hub/certs/files"


class DefaultConfig:
    # ------------------------------------------------------------------ general / Flask
    SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex())
    DEBUG = _env_bool("DEBUG", "False")

    # The UI and server messages are English only; the shape is kept for GET /api/language
    RAVEN_LANGUAGES = {"US": {"name": "English", "language_code": "en"}}

    RAVEN_DATA_FOLDER = _DATA_FOLDER
    RAVEN_LISTENER_ADDRESS = os.getenv("RAVEN_LISTENER_ADDRESS", "127.0.0.1")
    RAVEN_LISTENER_PORT = _env_int("RAVEN_LISTENER_PORT", 8081)
    RAVEN_MARTI_HTTP_PORT = _env_int("RAVEN_MARTI_HTTP_PORT", 8080)
    RAVEN_DOMAIN = os.getenv("RAVEN_DOMAIN") or None
    RAVEN_VPN_QR_DEFAULT_EXPIRATION_HOURS = _env_int("RAVEN_VPN_QR_DEFAULT_EXPIRATION_HOURS", 24)
    RAVEN_ONBOARD_TRUSTSTORE_PASSWORD = os.getenv("RAVEN_ONBOARD_TRUSTSTORE_PASSWORD", "atakatak")
    RAVEN_MARTI_HTTPS_PORT = _env_int("RAVEN_MARTI_HTTPS_PORT", 8443)
    RAVEN_ENABLE_TCP_STREAMING_PORT = _env_bool("RAVEN_ENABLE_TCP_STREAMING_PORT", "False")
    RAVEN_UDP_PORT = _env_int("RAVEN_UDP_PORT", 8087)
    RAVEN_TCP_STREAMING_PORT = _env_int("RAVEN_TCP_STREAMING_PORT", 8088)
    RAVEN_SSL_STREAMING_PORT = _env_int("RAVEN_SSL_STREAMING_PORT", 8089)
    RAVEN_STREAMING_INTERFACE = os.getenv("RAVEN_STREAMING_INTERFACE", "0.0.0.0")
    RAVEN_BACKUP_COUNT = _env_int("RAVEN_BACKUP_COUNT", 7)
    RAVEN_ENABLE_CHANNELS = _env_bool("RAVEN_ENABLE_CHANNELS", "True")

    # ------------------------------------------------------------------ RabbitMQ
    RAVEN_RABBITMQ_SERVER_ADDRESS = os.getenv("RAVEN_RABBITMQ_SERVER_ADDRESS", "127.0.0.1")
    RAVEN_RABBITMQ_USERNAME = os.getenv("RAVEN_RABBITMQ_USERNAME", "guest")
    RAVEN_RABBITMQ_PASSWORD = os.getenv("RAVEN_RABBITMQ_PASSWORD", "guest")
    # Per-message expiration in milliseconds (a string, as AMQP wants it); "0" disables
    RAVEN_RABBITMQ_TTL = "86400000"
    RAVEN_RABBITMQ_PREFETCH = 2

    # ------------------------------------------------------------------ TAK.gov link
    RAVEN_TAK_GOV_LINKED = False
    RAVEN_TAK_GOV_ACCESS_TOKEN = ""
    RAVEN_TAK_GOV_REFRESH_TOKEN = ""

    # ------------------------------------------------------------------ Web map
    # The web UI's map loads its base layers from public tile servers (OpenStreetMap, Google, Esri,
    # CARTO, a weather WMS), so every viewer's browser contacts them. Off: no Map page, no tiles and
    # /api/map_state refuses. The installers ask; servers without the key keep the map.
    RAVEN_MAP_ENABLE = _env_bool("RAVEN_MAP_ENABLE", "True")

    # ------------------------------------------------------------------ MediaMTX
    RAVEN_MEDIAMTX_ENABLE = _env_bool("RAVEN_MEDIAMTX_ENABLE", "True")
    RAVEN_MEDIAMTX_API_ADDRESS = os.getenv("RAVEN_MEDIAMTX_API_ADDRESS", "http://localhost:9997")
    RAVEN_MEDIAMTX_TOKEN = os.getenv("RAVEN_MEDIAMTX_TOKEN", secrets.token_urlsafe(22))

    # ------------------------------------------------------------------ Federation Hub
    RAVEN_FEDHUB_ENABLE = _env_bool("RAVEN_FEDHUB_ENABLE", "False")
    # No hub by default: install-fedhub.sh fills these in (see raven/fedhub_settings.py)
    RAVEN_FEDHUB_API_ADDRESS = os.getenv("RAVEN_FEDHUB_API_ADDRESS", "")
    RAVEN_FEDHUB_CLIENT_CERT = os.getenv(
        "RAVEN_FEDHUB_CLIENT_CERT", f"{_FEDHUB_FILES}/fedhub-admin.pem"
    )
    RAVEN_FEDHUB_CLIENT_KEY = os.getenv(
        "RAVEN_FEDHUB_CLIENT_KEY", f"{_FEDHUB_FILES}/fedhub-admin-unencrypted.key"
    )
    # A CA bundle path, or True for the system bundle
    RAVEN_FEDHUB_CA_BUNDLE = os.getenv("RAVEN_FEDHUB_CA_BUNDLE") or True
    # Empty: consumers use the host part of RAVEN_FEDHUB_BROKER_ADDRESS
    RAVEN_FEDHUB_HOST = os.getenv("RAVEN_FEDHUB_HOST", "")
    RAVEN_FEDHUB_ADMIN_P12 = os.getenv(
        "RAVEN_FEDHUB_ADMIN_P12", f"{_FEDHUB_FILES}/fedhub-admin.p12"
    )
    RAVEN_FEDHUB_ADMIN_P12_PASSWORD_FILE = os.getenv(
        "RAVEN_FEDHUB_ADMIN_P12_PASSWORD_FILE",
        "/opt/tak/federation-hub/certs/.fedhub-cert-passwords",
    )
    RAVEN_FEDHUB_FEDERATE_ENABLE = _env_bool("RAVEN_FEDHUB_FEDERATE_ENABLE", "False")
    RAVEN_FEDHUB_BROKER_ADDRESS = os.getenv("RAVEN_FEDHUB_BROKER_ADDRESS", "")
    RAVEN_FEDHUB_FEDERATE_CERT = os.getenv(
        "RAVEN_FEDHUB_FEDERATE_CERT", f"{_FEDHUB_FILES}/raven-federate.pem"
    )
    RAVEN_FEDHUB_FEDERATE_KEY = os.getenv(
        "RAVEN_FEDHUB_FEDERATE_KEY", f"{_FEDHUB_FILES}/raven-federate-unencrypted.key"
    )
    RAVEN_FEDHUB_FEDERATE_CA = os.getenv("RAVEN_FEDHUB_FEDERATE_CA", f"{_FEDHUB_FILES}/ca.pem")
    RAVEN_FEDHUB_FEDERATE_GROUP = os.getenv("RAVEN_FEDHUB_FEDERATE_GROUP", "Fed group")
    RAVEN_FEDHUB_FEDERATE_UID = os.getenv("RAVEN_FEDHUB_FEDERATE_UID", "raven-federate")
    RAVEN_FEDHUB_FEDERATE_USERNAME = os.getenv("RAVEN_FEDHUB_FEDERATE_USERNAME", "Server")

    # ------------------------------------------------------------------ security / devices
    RAVEN_SSL_VERIFICATION_MODE = _env_int("RAVEN_SSL_VERIFICATION_MODE", 2)
    RAVEN_SSL_CERT_HEADER = os.getenv("RAVEN_SSL_CERT_HEADER", "X-Ssl-Cert")
    RAVEN_CONNECTION_PROFILE_REQUIRE_AUTH = _env_bool(
        "RAVEN_CONNECTION_PROFILE_REQUIRE_AUTH", "True"
    )
    RAVEN_NODE_ID = os.getenv(
        "RAVEN_NODE_ID",
        "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(32)),
    )
    RAVEN_PROTECTED_USERNAMES = _env_list("RAVEN_PROTECTED_USERNAMES", ["Server", "Admin"])
    RAVEN_TURNSTILE_ENABLE = _env_bool("RAVEN_TURNSTILE_ENABLE", "False")
    RAVEN_TURNSTILE_SITE_KEY = os.getenv("RAVEN_TURNSTILE_SITE_KEY", "")
    RAVEN_TURNSTILE_SECRET_KEY = os.getenv("RAVEN_TURNSTILE_SECRET_KEY", "")
    # Empty: token auth on /api/metrics is disabled
    RAVEN_METRICS_TOKEN = os.getenv("RAVEN_METRICS_TOKEN", "")
    RAVEN_METRICS_FLUSH_INTERVAL = _env_int("RAVEN_METRICS_FLUSH_INTERVAL", 10)
    # Cap on concurrent device connections per streaming process
    RAVEN_EUD_MAX_CONNECTIONS = _env_int("RAVEN_EUD_MAX_CONNECTIONS", 2000)

    # ------------------------------------------------------------------ certificate authority
    RAVEN_CA_NAME = os.getenv("RAVEN_CA_NAME", "Raven-CA")
    RAVEN_CA_FOLDER = os.getenv("RAVEN_CA_FOLDER", os.path.join(_DATA_FOLDER, "ca"))
    RAVEN_CA_PASSWORD = os.getenv("RAVEN_CA_PASSWORD", "atakatak")
    RAVEN_CA_EXPIRATION_TIME = _env_int("RAVEN_CA_EXPIRATION_TIME", 3650)
    RAVEN_CA_COUNTRY = _CA_COUNTRY
    RAVEN_CA_STATE = _CA_STATE
    RAVEN_CA_CITY = _CA_CITY
    RAVEN_CA_ORGANIZATION = _CA_ORGANIZATION
    RAVEN_CA_ORGANIZATIONAL_UNIT = _CA_ORGANIZATIONAL_UNIT
    RAVEN_CA_SUBJECT = os.getenv(
        "RAVEN_CA_SUBJECT",
        f"/C={_CA_COUNTRY}/ST={_CA_STATE}/L={_CA_CITY}/O={_CA_ORGANIZATION}/OU={_CA_ORGANIZATIONAL_UNIT}",
    )

    # ------------------------------------------------------------------ processes
    RAVEN_COT_PARSER_PROCESSES = _env_int("RAVEN_COT_PARSER_PROCESSES", 1)

    # ------------------------------------------------------------------ LDAP
    RAVEN_ENABLE_LDAP = False
    RAVEN_LDAP_ADMIN_GROUP = "raven_admin"
    RAVEN_LDAP_COLOR_ATTRIBUTE = "colorAttribute"
    RAVEN_LDAP_ROLE_ATTRIBUTE = "roleAttribute"
    RAVEN_LDAP_CALLSIGN_ATTRIBUTE = "callsignAttribute"
    RAVEN_LDAP_PREFERENCE_ATTRIBUTE_PREFIX = "raven_"
    RAVEN_LDAP_GROUP_PREFIX = "raven_"
    # Flask-LDAP3-Login connection (placeholders until an admin configures LDAP)
    LDAP_HOST = "127.0.0.1"
    LDAP_BIND_USER_DN = "cn=admin,ou=users=dc=example,dc=com"
    LDAP_BIND_USER_PASSWORD = "password"
    LDAP_BASE_DN = LDAP_USER_DN = LDAP_GROUP_DN = ""

    # ------------------------------------------------------------------ logging
    RAVEN_LOG_ROTATE_WHEN = os.getenv("RAVEN_LOG_ROTATE_WHEN", "midnight")
    RAVEN_LOG_ROTATE_INTERVAL = _env_int("RAVEN_LOG_ROTATE_INTERVAL", 0)

    # ------------------------------------------------------------------ ADS-B
    RAVEN_ADSB_LAT = 40.744213
    RAVEN_ADSB_LON = -73.986939
    RAVEN_ADSB_RADIUS = 10
    RAVEN_ADSB_API_URL = "https://api.airplanes.live/v2/point/"
    RAVEN_ADSB_API_KEY = None
    RAVEN_ADSB_GROUP = "ADS-B"

    # ------------------------------------------------------------------ plugins
    RAVEN_ENABLE_PLUGINS = True
    # Empty: no plugin repository
    RAVEN_PLUGIN_REPO = os.getenv("RAVEN_PLUGIN_REPO", "")
    RAVEN_PLUGIN_PREFIXES = ["raven-", "raven_"]

    # ------------------------------------------------------------------ profiles and uploads
    RAVEN_PROFILE_MAP_SOURCES = True
    # Flask's request-body cap; larger bodies get HTTP 413
    MAX_CONTENT_LENGTH = _env_int("RAVEN_MAX_CONTENT_MB", 100) * 1024 * 1024
    RAVEN_DEVICE_PROFILE_MAX_FILE_MB = _env_int("RAVEN_DEVICE_PROFILE_MAX_FILE_MB", 100)
    RAVEN_DEVICE_PROFILE_MAX_TOTAL_MB = _env_int("RAVEN_DEVICE_PROFILE_MAX_TOTAL_MB", 500)
    RAVEN_DEVICE_PROFILE_MAX_FILES = _env_int("RAVEN_DEVICE_PROFILE_MAX_FILES", 50)
    RAVEN_ENABLE_MUMBLE_AUTHENTICATION = False
    RAVEN_IP_WHITELIST = ["127.0.0.1"]

    # ------------------------------------------------------------------ Meshtastic
    # One switch for the admin-only Meshtastic page, /api/meshtastic/* (404 when off) and the MQTT
    # bridge. Off by default.
    RAVEN_ENABLE_MESHTASTIC = _env_bool("RAVEN_ENABLE_MESHTASTIC", "False")
    RAVEN_MESHTASTIC_TOPIC = "raven"
    RAVEN_MESHTASTIC_PUBLISH_INTERVAL = 30
    RAVEN_MESHTASTIC_DOWNLINK_CHANNELS = []
    RAVEN_MESHTASTIC_NODEINFO_INTERVAL = 3
    RAVEN_MESHTASTIC_GROUP = "Meshtastic"

    # ------------------------------------------------------------------ old-data deletion window
    RAVEN_DELETE_OLD_DATA_SECONDS = _env_int("RAVEN_DELETE_OLD_DATA_SECONDS", 0)
    RAVEN_DELETE_OLD_DATA_MINUTES = _env_int("RAVEN_DELETE_OLD_DATA_MINUTES", 0)
    RAVEN_DELETE_OLD_DATA_HOURS = _env_int("RAVEN_DELETE_OLD_DATA_HOURS", 0)
    RAVEN_DELETE_OLD_DATA_DAYS = _env_int("RAVEN_DELETE_OLD_DATA_DAYS", 0)
    RAVEN_DELETE_OLD_DATA_WEEKS = _env_int("RAVEN_DELETE_OLD_DATA_WEEKS", 1)

    # ------------------------------------------------------------------ SQLAlchemy
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "SQLALCHEMY_DATABASE_URI", "postgresql+psycopg://raven_placeholder:POSTGRESQL_PASSWORD@127.0.0.1/raven"
    )
    SQLALCHEMY_ECHO = _env_bool("SQLALCHEMY_ECHO", "False")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_RECORD_QUERIES = False

    # ------------------------------------------------------------------ files
    ALLOWED_EXTENSIONS = _env_list(
        "ALLOWED_EXTENSIONS",
        [
            "zip",
            "xml",
            "txt",
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "gif",
            "kml",
            "kmz",
            "p12",
            "tif",
            "sqlite",
        ],
    )
    UPLOAD_FOLDER = _UPLOAD_FOLDER

    # ------------------------------------------------------------------ Flask-Security / sessions / CSRF
    SECURITY_PASSWORD_SALT = os.getenv(
        "SECURITY_PASSWORD_SALT", str(secrets.SystemRandom().getrandbits(128))
    )
    RAVEN_CORS_ORIGINS = []

    # Cookies: same-site only, and HTTPS-only unless overridden for a plain-HTTP lab setup
    SESSION_COOKIE_SAMESITE = REMEMBER_COOKIE_SAMESITE = "strict"
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", "True")
    REMEMBER_COOKIE_SECURE = _env_bool("REMEMBER_COOKIE_SECURE", "True")

    # The web UI is a single-page app talking JSON to /api
    SECURITY_URL_PREFIX = "/api"
    SECURITY_REDIRECT_BEHAVIOR = "spa"
    SECURITY_RETURN_GENERIC_RESPONSES = True  # don't reveal which half of a login was wrong

    # CSRF: the SPA echoes the XSRF-TOKEN cookie back; tokens never expire on their own
    SECURITY_CSRF_COOKIE_NAME = "XSRF-TOKEN"
    SECURITY_CSRF_PROTECT_MECHANISMS = ["session", "basic"]
    SECURITY_CSRF_IGNORE_UNAUTH_ENDPOINTS = True
    WTF_CSRF_CHECK_DEFAULT = False
    WTF_CSRF_TIME_LIMIT = None

    # Accounts are identified by username (there is no email address)
    SECURITY_USERNAME_ENABLE = SECURITY_USERNAME_REQUIRED = True
    SECURITY_USERNAME_MIN_LENGTH = 1
    SECURITY_MSG_USERNAME_DISALLOWED_CHARACTERS = (
        "Username can contain only letters, numbers, underscores, and periods",
        "error",
    )
    SECURITY_TRACKABLE = True  # last/current login time and address on each user

    # Passwords: users may change their own; no confirmation field
    SECURITY_CHANGEABLE = True
    SECURITY_CHANGE_URL = "/password/change"
    SECURITY_PASSWORD_LENGTH_MIN = 9
    SECURITY_PASSWORD_CONFIRM_REQUIRED = False

    # Tokens and login throttling
    SECURITY_TOKEN_MAX_AGE = 30 * 24 * 60 * 60  # seconds
    RAVEN_MISSION_TOKEN_DAYS = 30
    RAVEN_LOGIN_LOCKOUT_ATTEMPTS = 10  # 0 disables the lockout
    RAVEN_LOGIN_LOCKOUT_WINDOW_MINUTES = 15
    RAVEN_LOGIN_LOCKOUT_MINUTES = 15

    # The server sends no email at all: these and every other email-only Flask-Security
    # setting are forced again at startup (raven.no_email), whatever config.yml says
    SECURITY_REGISTERABLE = False
    SECURITY_CONFIRMABLE = False
    SECURITY_RECOVERABLE = False

    # Optional authenticator-app second factor
    SECURITY_TWO_FACTOR = True
    SECURITY_TWO_FACTOR_ENABLED_METHODS = ["authenticator"]
    SECURITY_TWO_FACTOR_ALWAYS_VALIDATE = False
    SECURITY_TOTP_SECRETS = {1: os.getenv("SECURITY_TOTP_SECRET", pyotp.random_base32())}
    SECURITY_TOTP_ISSUER = os.getenv("SECURITY_TOTP_ISSUER", "Raven")

    # ------------------------------------------------------------------ scheduler
    # Flask-APScheduler's own REST API stays off; raven_api.scheduler_api is the admin UI's API.
    SCHEDULER_API_ENABLED = False
    # (order = the order a fresh job store is filled in)
    JOBS = [
        _scheduled_job("delete_video_recordings", "interval", paused=True, minutes=1),
        _scheduled_job("purge_data", "cron", paused=True, day="*", hour=0, minute=0),
        _scheduled_job("delete_old_data", "interval", paused=True, minutes=1),
        _scheduled_job("reconcile_eud_status", "interval", minutes=1),
        _scheduled_job("expire_group_memberships", "interval", minutes=1),
        _scheduled_job("record_metrics_sample", "interval", minutes=1),
        _scheduled_job("apply_data_retention", "interval", minutes=10),
        # Nightly / weekly jobs run at odd minutes so they don't pile up on the hour
        _scheduled_job("refresh_crl", "cron", day="*", hour=3, minute=17),
        _scheduled_job("purge_logs", "cron", paused=True, day_of_week="sun", hour=3, minute=47),
    ]

"""Application factory and the `raven` web server entry point.

gevent must patch the standard library before anything else is imported in the process.
"""

from gevent import monkey

monkey.patch_all()

import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import traceback  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from logging.handlers import TimedRotatingFileHandler  # noqa: E402
from urllib.parse import quote  # noqa: E402

import colorlog  # noqa: E402
import pika  # noqa: E402
import pytz  # noqa: E402
import sqlalchemy.exc  # noqa: E402
import yaml  # noqa: E402
from apscheduler.jobstores.base import JobLookupError  # noqa: E402
from flask import Flask, request, session  # noqa: E402
from flask_cors import CORS  # noqa: E402
from flask_migrate import upgrade  # noqa: E402
from flask_security import (  # noqa: E402
    Security,
    SQLAlchemyUserDatastore,
    current_user,
    hash_password,
    uia_username_mapper,
)
from flask_security.models import fsqla_v3 as fsqla  # noqa: E402
from flask_security.signals import password_changed  # noqa: E402
from flask_wtf import CSRFProtect  # noqa: E402
from sqlalchemy import insert, select  # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

import raven  # noqa: E402
from raven.blueprints.cli import raven_cli, translate  # noqa: E402
from raven.defaultconfig import DefaultConfig  # noqa: E402
from raven.extensions import (  # noqa: E402
    apscheduler,
    babel,
    db,
    ldap_manager,
    logger,
    migrate,
    socketio,
)

try:
    from raven.mumble.mumble_ice_app import MumbleIceDaemon
except ImportError:
    MumbleIceDaemon = None
    print("Mumble auth not supported on this platform")

PACKAGE_DIR = os.path.dirname(os.path.abspath(raven.__file__))
BUNDLED_ICONSETS_PATH = os.path.join(PACKAGE_DIR, "data", "iconsets.sqlite")
MIGRATIONS_DIR = os.path.join(PACKAGE_DIR, "migrations")

# Feed jobs that no longer exist: dropped from config.yml's JOBS and from the job store
REMOVED_SCHEDULER_JOB_IDS = ("get_adsb_data", "ais")
# Jobs added after config.yml snapshots were first written; appended to old snapshots
ADDED_SCHEDULER_JOB_IDS = (
    "expire_group_memberships",
    "record_metrics_sample",
    "apply_data_retention",
    "refresh_crl",
    "purge_logs",
)
# Jobs that must run; one an older release left paused in the job store is resumed at start
ALWAYS_ON_SCHEDULER_JOB_IDS = ("reconcile_eud_status",)

_LOG_FORMAT = (
    "[%(asctime)s] - Raven[%(process)d] - %(module)s - %(funcName)s - %(lineno)d - "
    "%(levelname)s - %(message)s"
)

# (exchange, type, durable)
_EXCHANGES = (
    ("dms", "direct", True),
    ("cot_parser", "direct", True),
    ("chatrooms", "direct", True),
    ("missions", "topic", True),
    ("groups", "topic", True),
    ("firehose", "fanout", True),
    ("flask-socketio", "fanout", False),
    ("eud_control", "direct", True),
)


# ------------------------------------------------------------------------------------------ config


def api_cors_origins(config) -> list[str]:
    """Origins allowed to make credentialed cross-origin calls to /api: the server's own domain
    on its usual ports plus RAVEN_CORS_ORIGINS. Never "*"."""
    origins = []
    domain = (config.get("RAVEN_DOMAIN") or "").strip()
    if domain:
        origins.append(f"https://{domain}")
        origins += [f"https://{domain}:{port}" for port in (443, 8443, 8446)]

    extra = config.get("RAVEN_CORS_ORIGINS") or []
    if isinstance(extra, str):
        extra = extra.split(",")
    for origin in extra:
        origin = str(origin).strip()
        if not origin or origin == "*":
            continue
        origins.append(origin.rstrip("/"))

    unique = []
    for origin in origins:
        if origin not in unique:
            unique.append(origin)
    return unique


def drop_removed_jobs_from_config(app):
    for key in ("JOBS", "SCHEDULER_JOBS"):
        jobs = app.config.get(key)
        if not jobs:
            continue
        kept = []
        for job in jobs:
            if job.get("id") in REMOVED_SCHEDULER_JOB_IDS:
                continue
            # The persistent job store keeps admin-changed schedules; config must never replace them
            job = {k: v for k, v in job.items() if k != "replace_existing"}
            kept.append(job)
        app.config[key] = kept


def add_new_jobs_to_config(app):
    jobs = app.config.get("JOBS")
    if not isinstance(jobs, list):
        return
    present = {job.get("id") for job in jobs}
    for job in DefaultConfig.JOBS:
        if job["id"] in ADDED_SCHEDULER_JOB_IDS and job["id"] not in present:
            jobs.append(dict(job))
            present.add(job["id"])


def remove_obsolete_scheduler_jobs():
    for job_id in REMOVED_SCHEDULER_JOB_IDS:
        try:
            apscheduler.remove_job(job_id)
            logger.info(f"Removed obsolete scheduled job {job_id}")
        except JobLookupError:
            pass
        except Exception as e:
            logger.warning(f"Failed to remove obsolete scheduled job {job_id}: {e}")


def resume_always_on_jobs():
    for job_id in ALWAYS_ON_SCHEDULER_JOB_IDS:
        try:
            job = apscheduler.get_job(job_id)
            if job is not None and job.next_run_time is None:
                apscheduler.resume_job(job_id)
                logger.info(f"Resumed scheduled job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to resume scheduled job {job_id}: {e}")


# ------------------------------------------------------------------------------------------ babel


def get_locale():
    if "language" in session:
        return session["language"]
    from flask import current_app

    # Match the browser's Accept-Language against the offered language codes ("en", ...)
    codes = [entry["language_code"] for entry in current_app.config["RAVEN_LANGUAGES"].values()]
    return request.accept_languages.best_match(codes, default=codes[0] if codes else None)


def get_timezone():
    # Everything is UTC; the frontends convert
    return pytz.utc


# ------------------------------------------------------------------------------------------ logging


def setup_logging(app):
    level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    logger.setLevel(level)

    if sys.stdout.isatty():
        handler = colorlog.StreamHandler()
        handler.setFormatter(
            colorlog.ColoredFormatter("%(log_color)s" + _LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S %Z")
        )
        logger.addHandler(handler)

    logs = os.path.join(app.config["RAVEN_DATA_FOLDER"], "logs")
    os.makedirs(logs, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        os.path.join(logs, "raven.log"),
        when=app.config["RAVEN_LOG_ROTATE_WHEN"],
        interval=app.config["RAVEN_LOG_ROTATE_INTERVAL"],
        backupCount=app.config["RAVEN_BACKUP_COUNT"],
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(file_handler)


# ------------------------------------------------------------------------------------------ security


def _init_security(app):
    from raven.forms.SiteAccessLoginForm import SiteAccessLoginForm
    from raven.models.role import Role
    from raven.models.user import User
    from raven.models.WebAuthn import WebAuthn
    from raven.no_email import NoEmailMailUtil, apply_no_email_settings
    from raven.PasswordValidator import PasswordValidator
    from raven.UsernameValidator import UsernameValidator

    # No email of any kind, whatever config.yml says
    apply_no_email_settings(app.config)

    try:
        fsqla.FsModels.set_db_info(db)
    except sqlalchemy.exc.InvalidRequestError:
        pass  # already bound in this process

    datastore = SQLAlchemyUserDatastore(db, User, Role, WebAuthn)
    app.security = Security(
        app,
        datastore,
        mail_util_cls=NoEmailMailUtil,
        password_util_cls=PasswordValidator,
        username_util_cls=UsernameValidator,
        login_form=SiteAccessLoginForm,
    )


# Methods that change state, so need a CSRF token when the caller is a browser session
CSRF_CHECKED_METHODS = ("POST", "PUT", "PATCH", "DELETE")
# Machine callbacks without a browser session, and login endpoints that check CSRF themselves.
# (Everything outside /api, /Marti and /oauth included, is never checked by this hook.)
CSRF_EXEMPT_API_PATHS = ("/api/external_auth", "/api/mediamtx/webhook", "/api/ldap_login")
CSRF_EXEMPT_API_PREFIXES = ("/api/rabbitmq/",)


def _csrf_exempt_request(app) -> bool:
    path = request.path
    if not path.startswith("/api/"):
        return True
    if path in CSRF_EXEMPT_API_PATHS or path.startswith(CSRF_EXEMPT_API_PREFIXES):
        return True
    # Flask-Security's own views (login, logout, tf-*, password change, ...) do their own check
    if request.blueprint == app.config.get("SECURITY_BLUEPRINT_NAME", "security"):
        return True

    # Explicit API credentials. Browsers can't add these headers to a cross-site request without
    # a CORS preflight, so their presence means the caller isn't relying on the session cookie.
    token_header = app.config.get("SECURITY_TOKEN_AUTHENTICATION_HEADER", "Authentication-Token")
    if request.headers.get(token_header):
        return True
    if (request.headers.get("Authorization") or "").lower().startswith("bearer "):
        return True

    # Views or blueprints exempted through Flask-WTF
    csrf = app.extensions.get("csrf")
    if csrf is not None:
        if app.blueprints.get(request.blueprint) in getattr(csrf, "_exempt_blueprints", ()):
            return True
        view = app.view_functions.get(request.endpoint)
        if view is not None and f"{view.__module__}.{view.__name__}" in getattr(
            csrf, "_exempt_views", ()
        ):
            return True
    return False


def _init_api_csrf(app):
    """WTF_CSRF_CHECK_DEFAULT is off, so Flask-WTF only checks CSRF where Flask-Security's
    decorators ask for it (@auth_required), not on routes guarded by @roles_required /
    @roles_accepted alone. Check every state-changing /api request authenticated by the session
    cookie here, centrally. Token, bearer and client-certificate (no session) callers are not
    checked."""
    from flask_security.utils import get_request_attr
    from flask_wtf.csrf import CSRFError

    @app.before_request
    def check_api_csrf():
        if request.method not in CSRF_CHECKED_METHODS:
            return None
        if not app.config.get("WTF_CSRF_ENABLED", True):
            return None
        if _csrf_exempt_request(app):
            return None
        if _authenticated_user() is None or get_request_attr("fs_authn_via") != "session":
            return None  # no browser session to ride on

        csrf = app.extensions.get("csrf")
        try:
            if csrf is None:
                raise CSRFError("CSRF protection is not configured.")
            csrf.protect()
        except CSRFError as e:
            logger.info(f"Refused {request.method} {request.path}: {e.description}")
            return app.response_class(
                json.dumps({"meta": {"code": 400}, "response": {"errors": [e.description]}}),
                status=400,
                mimetype="application/json",
            )
        return None


def _security_url(config, key, default):
    return config.get("SECURITY_URL_PREFIX", "") + config.get(key, default)


def _authenticated_user():
    try:
        if current_user and current_user.is_authenticated:
            return current_user
    except Exception:
        pass
    return None


def _init_force_password_change(app):
    config = app.config
    change_url = _security_url(config, "SECURITY_CHANGE_URL", "/change")
    logout_url = _security_url(config, "SECURITY_LOGOUT_URL", "/logout")
    login_url = _security_url(config, "SECURITY_LOGIN_URL", "/login")
    tf_url = _security_url(config, "SECURITY_TWO_FACTOR_TOKEN_VALIDATION_URL", "/tf-validate")
    message = (
        "You must change your password before continuing. "
        f"Go to {change_url} to set a new one, then log in again."
    )

    @app.after_request
    def enforce_password_change(response):
        path = request.path
        if path in (change_url, logout_url, login_url, tf_url):
            if path in (login_url, tf_url) and response.status_code == 200 and response.is_json:
                try:
                    user = _authenticated_user()
                    if user is not None:
                        body = response.get_json()
                        if isinstance(body, dict):
                            if not isinstance(body.get("response"), dict):
                                body["response"] = {}
                            body["response"]["force_password_change"] = bool(
                                getattr(user, "force_password_change", False)
                            )
                            response.set_data(json.dumps(body))
                except Exception:
                    pass
            return response

        user = _authenticated_user()
        if user is not None and getattr(user, "force_password_change", False):
            return app.response_class(
                json.dumps({"meta": {"code": 403}, "response": {"errors": [message]}}),
                status=403,
                mimetype="application/json",
            )
        return response

    def clear_flag(sender, user=None, **kwargs):
        if user is not None and getattr(user, "force_password_change", False):
            user.force_password_change = False
            db.session.commit()

    password_changed.connect(clear_flag, app, weak=False)


# ------------------------------------------------------------------------------------------ extensions


def _declare_exchanges(app):
    credentials = pika.PlainCredentials(
        app.config["RAVEN_RABBITMQ_USERNAME"], app.config["RAVEN_RABBITMQ_PASSWORD"]
    )
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            app.config["RAVEN_RABBITMQ_SERVER_ADDRESS"], credentials=credentials
        )
    )
    channel = connection.channel()
    for name, kind, durable in _EXCHANGES:
        channel.exchange_declare(exchange=name, exchange_type=kind, durable=durable)
    channel.close()
    connection.close()


def init_extensions(app):
    from raven import cert_revocation
    from raven.certificate_authority import CertificateAuthority
    from raven.sql_jobstore import SQLJobStore

    config = app.config
    debug = config.get("DEBUG")

    db.init_app(app)
    migrate.init_app(app, db, directory=MIGRATIONS_DIR)

    logger.info(f"Raven {raven.__version__}")
    logger.info("Loading the database...")
    with app.app_context():
        upgrade(directory=MIGRATIONS_DIR)
    # Alembic's logging config disables existing loggers and adds a handler of its own
    logger.disabled = False
    if logger.parent is not None and logger.parent.handlers:
        logger.parent.removeHandler(logger.parent.handlers[-1])
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    config["SCHEDULER_JOBSTORES"] = {"default": SQLJobStore(url=config["SQLALCHEMY_DATABASE_URI"])}

    identity = [{"username": {"mapper": uia_username_mapper, "case_insensitive": True}}]
    if config.get("RAVEN_ENABLE_LDAP"):
        identity.append({"ldap": {}})
        logger.info("Enabling LDAP")
        ldap_manager.init_app(app)
    config["SECURITY_USER_IDENTITY_ATTRIBUTES"] = identity

    from raven.no_email import reset_email_two_factor

    with app.app_context():
        try:
            reset_email_two_factor()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to move email-2FA users to an authenticator app: {e}")

    CertificateAuthority(logger, app).create_ca()
    try:
        cert_revocation.ensure_crl(config["RAVEN_CA_FOLDER"], config["RAVEN_CA_PASSWORD"], logger)
    except Exception as e:
        logger.error(f"Failed to check the CA's CRL: {e}")

    CORS(
        app,
        resources={
            r"/api/*": {"origins": api_cors_origins(config)},
            r"/Marti/*": {"origins": "*"},
            r"/oauth/*": {"origins": "*"},
            r"/files/*": {"origins": "*"},
        },
        supports_credentials=True,
    )

    CSRFProtect(app)

    user = quote(str(config["RAVEN_RABBITMQ_USERNAME"]), safe="")
    password = quote(str(config["RAVEN_RABBITMQ_PASSWORD"]), safe="")
    socketio.init_app(
        app,
        logger=logger if debug else False,
        ping_timeout=1,
        message_queue=f"amqp://{user}:{password}@{config['RAVEN_RABBITMQ_SERVER_ADDRESS']}",
    )

    _declare_exchanges(app)

    _init_security(app)
    _init_api_csrf(app)

    babel.init_app(app, locale_selector=get_locale, timezone_selector=get_timezone)

    if not apscheduler.running:
        from raven.models import import_all_models

        # Jobs can fire as soon as the scheduler starts, so every model must be mapped first
        import_all_models()
        add_new_jobs_to_config(app)
        drop_removed_jobs_from_config(app)
        apscheduler.init_app(app)
        apscheduler.start(paused=False)
        remove_obsolete_scheduler_jobs()
        resume_always_on_jobs()


def _set_mediamtx_token(app):
    if not app.config.get("RAVEN_MEDIAMTX_ENABLE"):
        logger.info("MediaMTX is disabled")
        return
    path = os.path.join(app.config["RAVEN_DATA_FOLDER"], "mediamtx", "mediamtx.yml")
    try:
        with open(path) as f:
            contents = f.read()
        if "MTX_TOKEN" not in contents:
            return
        contents = contents.replace("MTX_TOKEN", str(app.config["RAVEN_MEDIAMTX_TOKEN"]))
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            f.write(contents)
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)
    except Exception as e:
        logger.error(f"Failed to set MediaMTX token: {e}")


def _load_config_yml(app) -> bool:
    path = os.path.join(app.config["RAVEN_DATA_FOLDER"], "config.yml")
    if not os.path.exists(path):
        return False
    with open(path) as f:
        app.config.from_mapping(yaml.safe_load(f) or {})
    return True


def create_app(cli=True):
    app = Flask(__name__)
    app.config.from_object(DefaultConfig)
    setup_logging(app)

    from raven.admin_view import redact_ips_for_viewer
    from raven.blueprints.marti_api import marti_blueprint
    from raven.blueprints.raven_api import raven_api
    from raven.blueprints.raven_socketio import raven_socketio_blueprint
    from raven.blueprints.scheduled_jobs import scheduler_blueprint

    if not cli:
        if not _load_config_yml(app):
            from raven.config_file import write_default_config

            logger.info("Creating config.yml")
            write_default_config(os.path.join(app.config["RAVEN_DATA_FOLDER"], "config.yml"))

        _set_mediamtx_token(app)
        init_extensions(app)
        _init_force_password_change(app)

        from raven.audit import init_audit
        from raven.login_lockout import init_lockout

        init_audit(app)
        init_lockout(app)

        app.register_blueprint(marti_blueprint)
        app.register_blueprint(raven_api)
        app.register_blueprint(raven_socketio_blueprint)
        app.register_blueprint(scheduler_blueprint)
        # Registered after the force-password hook, so Flask runs it first
        app.after_request(redact_ips_for_viewer)

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
    else:
        app.cli.add_command(raven_cli, "raven")
        app.cli.add_command(translate, "translate")

        if _load_config_yml(app):
            db.init_app(app)
            migrate.init_app(app, db, directory=MIGRATIONS_DIR)

        CSRFProtect(app)
        _init_security(app)
        _init_api_csrf(app)
        # The API views translate their messages, which needs Flask-Babel on the app
        babel.init_app(app, locale_selector=get_locale, timezone_selector=get_timezone)

        app.register_blueprint(marti_blueprint)
        app.register_blueprint(raven_api)
        app.after_request(redact_ips_for_viewer)
        app.register_blueprint(raven_socketio_blueprint)
        app.register_blueprint(scheduler_blueprint)

    return app


# ------------------------------------------------------------------------------------------ startup


def create_default_groups(app):
    from raven.models.Group import Group, GroupTypeEnum
    from raven.models.GroupUser import GroupUser
    from raven.models.user import User

    config = app.config
    with app.app_context():
        if not config.get("RAVEN_ENABLE_LDAP"):
            if (
                db.session.execute(select(Group).filter_by(name="__ANON__")).scalar_one_or_none()
                is None
            ):
                anon = Group()
                anon.name = "__ANON__"
                anon.type = GroupTypeEnum.SYSTEM
                anon.bitpos = 2
                db.session.add(anon)
                db.session.commit()
                logger.info("Created the __ANON__ group")

        if not config.get("RAVEN_FEDHUB_FEDERATE_ENABLE"):
            return

        group_name = config["RAVEN_FEDHUB_FEDERATE_GROUP"]
        group = db.session.execute(select(Group).filter_by(name=group_name)).scalar_one_or_none()
        if group is None:
            group = Group()
            group.name = group_name
            group.type = GroupTypeEnum.SYSTEM
            group.bitpos = group.get_next_bitpos()
            db.session.add(group)
            db.session.commit()
            logger.info(f"Created the {group_name} group")

        username = config["RAVEN_FEDHUB_FEDERATE_USERNAME"]
        user = db.session.execute(select(User).filter_by(username=username)).scalar_one_or_none()
        if user is None:
            user = app.security.datastore.create_user(
                username=username,
                password=hash_password(secrets.token_urlsafe(32)),
                active=False,
            )
            db.session.commit()
            logger.info(f"Created the {username} user")

        membership = db.session.execute(
            select(GroupUser).filter_by(user_id=user.id, group_id=group.id, direction=Group.IN)
        ).scalar_one_or_none()
        if membership is None:
            db.session.add(GroupUser(user_id=user.id, group_id=group.id, direction=Group.IN))
            db.session.commit()
            logger.info(f"Added {username} to {group_name}")


def load_bundled_icons(path=BUNDLED_ICONSETS_PATH) -> int:
    """Copies the icon sets shipped in the package into the icons table (inside an app context).
    The file is opened read-only; nothing is downloaded or written to the data folder."""
    from raven.models.Icon import Icon

    connection = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("SELECT * FROM icons")]
    finally:
        connection.close()

    for row in rows:
        db.session.execute(insert(Icon.__table__).values(**row))
    db.session.commit()
    return len(rows)


def main(app):
    from raven.models.Icon import Icon
    from raven.models.role import Role
    from raven.models.user import User

    with app.app_context():
        try:
            if db.session.execute(select(Icon.id).limit(1)).first() is None:
                logger.info("Loading bundled icons...")
                load_bundled_icons()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to load the bundled icons: {e}")

    if app.config.get("DEBUG"):
        logger.info("Starting in debug mode")
    else:
        logger.info("Starting in production mode")

    with app.app_context():
        datastore = app.security.datastore
        datastore.find_or_create_role(name="user", permissions={"user-read", "user-write"})
        datastore.find_or_create_role(name="administrator", permissions={"administrator"})
        datastore.find_or_create_role(name="admin_viewer", permissions={"admin-read"})

        roles_users = User.roles.property.secondary
        administrator = db.session.execute(
            select(User.id)
            .join(roles_users, roles_users.c.user_id == User.id)
            .join(Role, Role.id == roles_users.c.role_id)
            .where(Role.name == "administrator")
            .limit(1)
        ).first()
        if administrator is None:
            logger.info(
                "Creating administrator account. The password is 'password' and must be changed "
                "at the first sign-in"
            )
            datastore.create_user(
                username="administrator",
                password=hash_password("password"),
                roles=["administrator"],
                force_password_change=True,
            )
        db.session.commit()

    if app.config.get("RAVEN_ENABLE_MESHTASTIC"):
        from raven.controllers.meshtastic_controller import MeshtasticController

        controller = MeshtasticController(app.app_context())
        # Both spellings: older code read the misspelled one
        app.meshtastic_thread = controller
        app.mestastic_thread = controller
    else:
        app.meshtastic_thread = None
        app.mestastic_thread = None

    if app.config.get("RAVEN_ENABLE_MUMBLE_AUTHENTICATION"):
        try:
            if MumbleIceDaemon is None:
                raise RuntimeError("Mumble auth not supported on this platform")
            daemon = MumbleIceDaemon(app, logger)
            if isinstance(daemon, threading.Thread):
                daemon.daemon = True
                daemon.start()
            else:
                threading.Thread(target=daemon.run, daemon=True).start()
        except Exception as e:
            logger.error(f"Failed to start the Mumble authentication daemon: {e}")

    if app.config.get("RAVEN_ENABLE_PLUGINS"):
        try:
            from raven.plugins.Plugin import Plugin
            from raven.plugins.PluginManager import PluginManager

            app.plugin_manager = PluginManager(Plugin.group, app)
            app.plugin_manager.load_plugins()
            app.plugin_manager.activate(app)
        except Exception as e:
            logger.error(f"Failed to load plugins: {e}")
            logger.debug(traceback.format_exc())

    app.start_time = datetime.now(timezone.utc)

    create_default_groups(app)

    debug = bool(app.config.get("DEBUG"))
    try:
        socketio.run(
            app,
            host=app.config["RAVEN_LISTENER_ADDRESS"],
            port=app.config["RAVEN_LISTENER_PORT"],
            debug=debug,
            log_output=debug,
            use_reloader=False,
        )
    except KeyboardInterrupt:
        logger.warning("Caught CTRL+C, exiting...")
        if app.config.get("RAVEN_ENABLE_PLUGINS") and hasattr(app, "plugin_manager"):
            app.plugin_manager.stop_plugins()


def start():
    app = create_app(cli=False)
    main(app)

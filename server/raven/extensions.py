"""Process-wide singletons shared by the web app, the services and the scheduled jobs.

Importers rely on these objects' identity, so they are created once here and bound to an app
later (init_app).
"""

import colorlog
from flask_apscheduler import APScheduler
from flask_babel import Babel
from flask_ldap3_login import LDAP3LoginManager
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

from raven.models.Base import Base

logger = colorlog.getLogger("Raven")

# Database and schema migrations
db = SQLAlchemy(model_class=Base)
migrate = Migrate()

# Web-facing services
socketio = SocketIO(async_mode="gevent")  # the server runs under gevent
babel = Babel()
ldap_manager = LDAP3LoginManager()

# Background jobs
apscheduler = APScheduler()

_POSTGRES_DRIVERS = ("psycopg", "pg8000", "asyncpg")


@event.listens_for(Engine, "connect")
def _use_utc_sessions(dbapi_connection, connection_record):
    """Every PostgreSQL session runs in UTC, so naive timestamp columns hold UTC wall-clock time
    whatever the server's local time zone is. Other drivers (SQLite, ...) are left alone."""
    module = type(dbapi_connection).__module__ or ""
    if not module.startswith(_POSTGRES_DRIVERS):
        return

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET TIME ZONE 'UTC'")
    finally:
        cursor.close()

    # Commit, so a rollback of the connection's first transaction can't undo the setting
    if not getattr(dbapi_connection, "autocommit", False):
        dbapi_connection.commit()

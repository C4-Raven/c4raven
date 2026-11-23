"""Alembic environment for Raven (Flask-Migrate, single database).

Flask-Migrate supplies the migrations directory and its ``configure_args``
(``compare_type=True`` and ``render_as_batch=True`` by default); this module
wires those into Alembic together with the Flask app's engine and metadata.

Note: ``fileConfig`` below installs a root "console" StreamHandler from
``alembic.ini``. ``raven.app.init_extensions`` relies on that handler being
the last one on the root logger (it pops it after ``upgrade()``), so keep the
logging configuration in place.
"""

import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

log = logging.getLogger("alembic.env")


def _migrate_ext():
    return current_app.extensions["migrate"]


def _engine():
    db = _migrate_ext().db
    try:
        # Flask-SQLAlchemy < 3
        return db.get_engine()
    except (TypeError, AttributeError):
        # Flask-SQLAlchemy >= 3
        return db.engine


def _engine_url() -> str:
    url = _engine().url
    try:
        text = url.render_as_string(hide_password=False)
    except AttributeError:
        text = str(url)
    # ConfigParser interpolation treats "%" specially.
    return text.replace("%", "%%")


def _target_metadata():
    db = _migrate_ext().db
    metadatas = getattr(db, "metadatas", None)
    if metadatas is not None:
        return metadatas[None]
    return db.metadata


config.set_main_option("sqlalchemy.url", _engine_url())


def _skip_empty_autogenerate(ctx, revision, directives):
    """Do not write an autogenerate revision that would contain no operations."""
    if not getattr(config.cmd_opts, "autogenerate", False):
        return
    script = directives[0]
    if script.upgrade_ops.is_empty():
        directives[:] = []
        log.info("No changes in schema detected.")


def run_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=_target_metadata(),
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    options = _migrate_ext().configure_args
    if options.get("process_revision_directives") is None:
        options["process_revision_directives"] = _skip_empty_autogenerate

    with _engine().connect() as connection:
        context.configure(connection=connection, target_metadata=_target_metadata(), **options)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()

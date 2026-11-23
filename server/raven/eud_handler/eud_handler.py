"""Entry point of the EUD streaming services: `eud_handler --ssl` (8089), `eud_handler` (plain TCP
8088, only when RAVEN_ENABLE_TCP_STREAMING_PORT) and `eud_handler --udp` (8087)."""

import argparse
import logging
import os
import sys
import time
from logging.handlers import TimedRotatingFileHandler

import colorlog
import sqlalchemy.exc
from flask_babel import gettext
from sqlalchemy import update

from raven import metrics
from raven.eud_handler.EudHandler import EudHandler, build_flask_app
from raven.eud_handler.EudHandlerSSL import EudHandlerSSL
from raven.eud_handler.EudServer import EudServer, plaintext_tcp_enabled
from raven.eud_handler.EudServerSSL import EudServerSSL
from raven.eud_handler.EudServerUdp import EudServerUdp
from raven.extensions import db, logger

SCHEMA_WAIT_SECONDS = 120
_LOG_FORMAT = (
    "[%(asctime)s] - eud_handler[%(process)d] - %(module)s - %(funcName)s - %(lineno)d - %(levelname)s - %(message)s"
)


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssl", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--udp", action=argparse.BooleanOptionalAction, default=False, help=gettext("UDP Server"))
    return parser


def _import_args():
    try:
        return _parser().parse_known_args()[0]
    except BaseException:
        return argparse.Namespace(ssl=False, udp=False)


# The log file name depends on the transport, so importing this module parses sys.argv
args = _import_args()


def _log_name():
    if args.ssl:
        return "eud_handler_ssl.log"
    if args.udp:
        return "eud_handler_udp.log"
    return "eud_handler_tcp.log"


def setup_logging(app):
    level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    logger.setLevel(level)

    if sys.stdout.isatty():
        handler = colorlog.StreamHandler()
        handler.name = "eud_handler"
        handler.setFormatter(colorlog.ColoredFormatter("%(log_color)s" + _LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S %Z"))
        logger.addHandler(handler)

    logs = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "logs")
    os.makedirs(logs, exist_ok=True)
    file_handler = TimedRotatingFileHandler(
        os.path.join(logs, _log_name()),
        when=app.config.get("RAVEN_LOG_ROTATE_WHEN"),
        interval=app.config.get("RAVEN_LOG_ROTATE_INTERVAL"),
        backupCount=app.config.get("RAVEN_BACKUP_COUNT"),
    )
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    logger.addHandler(file_handler)


def create_app():
    return build_flask_app(__name__, configure_logging=setup_logging)


app = create_app()


def reconcile_stale_status():
    """Nothing is connected to a process that just started: Connected rows are stale."""
    from raven.models.EUD import EUD

    deadline = time.monotonic() + SCHEMA_WAIT_SECONDS
    while True:
        try:
            db.session.execute(
                update(EUD)
                .where(EUD.last_status == "Connected")
                .values(last_status="Disconnected")
                .execution_options(synchronize_session=False)
            )
            db.session.commit()
            return
        except sqlalchemy.exc.ProgrammingError:
            db.session.rollback()
            if time.monotonic() >= deadline:
                raise
            logger.info("Waiting for raven to create the database schema...")
            time.sleep(2)


def _start_flusher(transport, gauges_fn):
    counters = metrics.install(metrics.SharedCounters(metrics.EUD_HANDLER_SERIES))
    flusher = metrics.MetricsFlusher(app, counters, f"eud_handler_{transport}", f'transport="{transport}"', gauges_fn)
    flusher.start()
    return flusher


def main():
    options = _parser().parse_args()
    if options.ssl and options.udp:
        logger.error("Cannot use --ssl and --udp at the same time")
        return

    if not options.ssl and not options.udp and not plaintext_tcp_enabled(app.config):
        logger.warning(
            f"The plain TCP streaming port (RAVEN_TCP_STREAMING_PORT {app.config.get('RAVEN_TCP_STREAMING_PORT')}) "
            "is disabled; set RAVEN_ENABLE_TCP_STREAMING_PORT to open it. Devices use the SSL port "
            f"(RAVEN_SSL_STREAMING_PORT {app.config.get('RAVEN_SSL_STREAMING_PORT')})."
        )
        # Never exit: raven.service requires this unit
        flusher = _start_flusher("tcp", lambda: {("raven_eud_connections", 'transport="tcp"'): 0})
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Shutting down server")
        finally:
            flusher.stop()
        return

    with app.app_context():
        reconcile_stale_status()

    interface = app.config.get("RAVEN_STREAMING_INTERFACE")
    if options.ssl:
        transport = "ssl"
        port = app.config.get("RAVEN_SSL_STREAMING_PORT")
        server = EudServerSSL((interface, port), EudHandlerSSL, logger, app)
        logger.info(f"Started SSL server on port {port}")
    elif options.udp:
        transport = "udp"
        server = EudServerUdp((interface, app.config.get("RAVEN_UDP_PORT")), EudHandler, logger, app)
    else:
        transport = "tcp"
        port = app.config.get("RAVEN_TCP_STREAMING_PORT")
        server = EudServer((interface, port), EudHandler, logger, app)
        logger.info(f"Started TCP server on port {port}")

    if transport == "udp":

        def gauges():
            return {}

    else:

        def gauges():
            return {("raven_eud_connections", f'transport="{transport}"'): len(server.active_children or ())}

    flusher = _start_flusher(transport, gauges)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server")
    finally:
        flusher.stop()


if __name__ == "__main__":
    main()

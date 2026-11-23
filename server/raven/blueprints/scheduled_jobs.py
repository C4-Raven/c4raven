"""Functions run by APScheduler (DefaultConfig.JOBS refers to them as
raven.blueprints.scheduled_jobs:<name>).

A job can fire in a process where nothing has imported the models yet, so each one imports them
all first and runs inside the scheduler's app context. Collaborators are looked up from their
modules at call time so tests can replace them.
"""

import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import tostring

import pika
import requests
from flask import Blueprint
from sqlalchemy import delete, select, text

from raven.extensions import apscheduler, db, logger, socketio
from raven.functions import (
    datetime_from_iso8601_string,
    generate_delete_cot,
    iso8601_string_from_datetime,
)

scheduler_blueprint = Blueprint("scheduler_blueprint", __name__)


@contextmanager
def _job_context():
    from raven.models import import_all_models

    import_all_models()
    with apscheduler.app.app_context():
        yield apscheduler.app


def _rabbitmq_connection(app):
    credentials = pika.PlainCredentials(
        app.config["RAVEN_RABBITMQ_USERNAME"], app.config["RAVEN_RABBITMQ_PASSWORD"]
    )
    return pika.BlockingConnection(
        pika.ConnectionParameters(
            app.config["RAVEN_RABBITMQ_SERVER_ADDRESS"], credentials=credentials
        )
    )


def get_airplanes_live_data():
    """Removed feed. Kept so stale job rows that still name it resolve."""


# --------------------------------------------------------------------------------- recordings


def delete_video_recordings():
    from raven.models.VideoRecording import VideoRecording

    with _job_context() as app:
        db.session.execute(delete(VideoRecording))
        db.session.commit()

        address = app.config["RAVEN_MEDIAMTX_API_ADDRESS"]
        try:
            r = requests.get(f"{address}/v3/recordings/list")
            if r.status_code == 200:
                for item in r.json().get("items", []):
                    for segment in item.get("segments", []):
                        d = requests.delete(
                            f"{address}/v3/recordings/deletesegment",
                            params={"start": segment["start"], "path": item["name"]},
                        )
                        if d.status_code != 200:
                            logger.error(
                                f"Failed to delete recording segment {item['name']} "
                                f"{segment['start']}: {d.status_code} {d.text}"
                            )
        except Exception as e:
            logger.error(f"Failed to delete recordings: {e}")


# --------------------------------------------------------------------------------- purge everything


def purge_data():
    from raven.models.Alert import Alert
    from raven.models.CasEvac import CasEvac
    from raven.models.Certificate import Certificate
    from raven.models.Chatrooms import Chatroom
    from raven.models.ChatroomsUids import ChatroomsUids
    from raven.models.CoT import CoT
    from raven.models.DataPackage import DataPackage
    from raven.models.EUD import EUD
    from raven.models.GeoChat import GeoChat
    from raven.models.Marker import Marker
    from raven.models.Mission import Mission
    from raven.models.MissionChange import MissionChange
    from raven.models.MissionContentMission import MissionContentMission
    from raven.models.MissionRole import MissionRole
    from raven.models.MissionUID import MissionUID
    from raven.models.Point import Point
    from raven.models.RBLine import RBLine
    from raven.models.Team import Team
    from raven.models.VideoStream import VideoStream
    from raven.models.ZMIST import ZMIST

    delete_video_recordings()

    # Children before parents
    models = (
        ZMIST,
        VideoStream,
        Alert,
        CasEvac,
        Certificate,
        ChatroomsUids,
        DataPackage,
        Marker,
        GeoChat,
        Point,
        RBLine,
        Chatroom,
        CoT,
        MissionRole,
        MissionContentMission,
        MissionUID,
        MissionChange,
        Mission,
        EUD,
        Team,
    )
    with _job_context():
        for model in models:
            db.session.execute(delete(model))
        db.session.commit()
        logger.info("Purged all data")


# --------------------------------------------------------------------------------- EUD status


def reconcile_eud_status():
    """Marks EUDs disconnected when they have sent nothing for 3 minutes (PostgreSQL SQL)."""
    from raven.models.EUD import EUD

    with _job_context():
        stale = (
            db.session.execute(
                select(EUD).where(
                    EUD.last_status == "Connected",
                    EUD.last_event_time <= text("now() - interval '3 minutes'"),
                    (EUD.last_rx_at.is_(None))
                    | (EUD.last_rx_at <= text("timezone('UTC', now()) - interval '3 minutes'")),
                )
            )
            .scalars()
            .all()
        )
        if not stale:
            return

        for eud in stale:
            eud.last_status = "Disconnected"
        db.session.commit()

        for eud in stale:
            try:
                socketio.emit("eud", eud.to_json(), namespace="/socket.io")
            except Exception as e:
                logger.warning(f"Failed to send the status of {eud.uid}: {e}")


# --------------------------------------------------------------------------------- old data


def _publish_delete(channel, app, groups, uid, cot_type):
    body = json.dumps(
        {
            "cot": tostring(generate_delete_cot(uid, cot_type)).decode("utf-8"),
            "uid": app.config["RAVEN_NODE_ID"],
        }
    )
    properties = pika.BasicProperties(expiration=app.config["RAVEN_RABBITMQ_TTL"])
    from raven.models.Group import Group

    for group in groups:
        channel.basic_publish(
            exchange="groups",
            routing_key=f"{group.name}.{Group.OUT}",
            body=body,
            properties=properties,
        )
    channel.basic_publish(exchange="firehose", routing_key="", body=body, properties=properties)


def delete_old_data():
    from raven.models.Alert import Alert
    from raven.models.CoT import CoT
    from raven.models.EUD import EUD
    from raven.models.GeoChat import GeoChat
    from raven.models.Group import Group
    from raven.models.Marker import Marker
    from raven.models.MissionChange import MissionChange
    from raven.models.MissionRole import MissionRole
    from raven.models.MissionUID import MissionUID
    from raven.models.Point import Point
    from raven.models.RBLine import RBLine

    with _job_context() as app:
        config = app.config
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=config["RAVEN_DELETE_OLD_DATA_SECONDS"],
            minutes=config["RAVEN_DELETE_OLD_DATA_MINUTES"],
            hours=config["RAVEN_DELETE_OLD_DATA_HOURS"],
            days=config["RAVEN_DELETE_OLD_DATA_DAYS"],
            weeks=config["RAVEN_DELETE_OLD_DATA_WEEKS"],
        )

        connection = _rabbitmq_connection(app)
        channel = connection.channel()
        try:
            # A list, not a one-shot result, so every deleted item is announced to every group
            groups = db.session.execute(select(Group)).scalars().all()

            for marker in db.session.execute(select(Marker)).scalars().all():
                if datetime_from_iso8601_string(marker.production_time) <= cutoff:
                    _publish_delete(channel, app, groups, marker.uid, marker.cot.type)
                    db.session.delete(marker)

            for alert in (
                db.session.execute(select(Alert).where(Alert.start_time <= cutoff)).scalars().all()
            ):
                _publish_delete(channel, app, groups, alert.uid, alert.cot.type)
                db.session.delete(alert)

            for line in (
                db.session.execute(select(RBLine).where(RBLine.timestamp <= cutoff)).scalars().all()
            ):
                _publish_delete(channel, app, groups, line.uid, line.cot.type)
                db.session.delete(line)

            db.session.execute(delete(GeoChat).where(GeoChat.timestamp <= cutoff))

            naive_cutoff = cutoff.replace(tzinfo=None)
            db.session.execute(delete(MissionRole).where(MissionRole.createTime <= naive_cutoff))
            db.session.execute(delete(MissionUID).where(MissionUID.timestamp <= naive_cutoff))
            db.session.execute(delete(MissionChange).where(MissionChange.timestamp <= naive_cutoff))
            db.session.execute(delete(EUD).where(EUD.last_event_time <= naive_cutoff))
            db.session.execute(delete(Point).where(Point.timestamp <= naive_cutoff))
            db.session.execute(delete(CoT).where(CoT.timestamp <= naive_cutoff))
            db.session.commit()
        finally:
            try:
                channel.close()
                connection.close()
            except Exception as e:
                logger.debug(f"Failed to close the RabbitMQ connection: {e}")

        logger.info(f"Deleted data older than {iso8601_string_from_datetime(cutoff)}")


# --------------------------------------------------------------------------------- groups, metrics, retention


def expire_group_memberships():
    from raven import group_policy

    with _job_context():
        try:
            expired = group_policy.expire_memberships()
            if expired:
                logger.info(f"Expired {expired} group membership(s)")
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to expire group memberships: {e}")


def record_metrics_sample():
    from raven import metrics

    with _job_context():
        try:
            metrics.take_sample(db.session)
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to record a metrics sample: {e}")


def apply_data_retention():
    from raven import retention

    with _job_context():
        try:
            retention.apply_retention()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Data retention failed: {e}")


# --------------------------------------------------------------------------------- CRL


def refresh_crl():
    from raven import cert_revocation

    with _job_context() as app:
        folder = app.config["RAVEN_CA_FOLDER"]
        try:
            cert_revocation.ensure_crl(folder, app.config["RAVEN_CA_PASSWORD"], logger)
        except Exception as e:
            try:
                next_update = cert_revocation.crl_next_update(folder)
            except Exception:
                next_update = None
            if next_update is None:
                message = f"Failed to refresh the CA's CRL (it is missing): {e}"
            else:
                message = (
                    f"Failed to refresh the CA's CRL (it expires {next_update.isoformat()}): {e}"
                )
            cert_revocation.report_crl_failure(apscheduler.app, message, logger)


# --------------------------------------------------------------------------------- logs

# <stem>.log, then optionally .YYYY-MM-DD[_HH[-MM[-SS]]] or .<n> (rotated)
_LOG_NAME_RE = re.compile(
    r"(?P<stem>[A-Za-z0-9_.-]+?)\.log"
    r"(?P<suffix>\.\d{4}-\d{2}-\d{2}(?:_\d{2}(?:-\d{2}(?:-\d{2})?)?)?|\.\d+)?"
)


def purge_logs():
    with _job_context() as app:
        logs = os.path.join(app.config["RAVEN_DATA_FOLDER"], "logs")
        emptied = deleted = freed = 0

        for folder, archived in ((logs, False), (os.path.join(logs, "archive"), True)):
            if not os.path.isdir(folder):
                continue
            for name in sorted(os.listdir(folder)):
                match = _LOG_NAME_RE.fullmatch(name)
                if not match:
                    continue
                path = os.path.join(folder, name)
                try:
                    info = os.lstat(path)
                    if not stat.S_ISREG(info.st_mode):
                        continue  # symlinks and anything else that isn't a plain file
                    if archived or match.group("suffix"):
                        os.remove(path)
                        deleted += 1
                    else:
                        # Truncated in place: the services keep writing through their open handles
                        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
                        try:
                            os.ftruncate(fd, 0)
                        finally:
                            os.close(fd)
                        emptied += 1
                    freed += info.st_size
                except OSError as e:
                    logger.warning(f"Purge logs: couldn't clean {path}: {e}")

        logger.info(
            f"Purge logs: emptied {emptied} and deleted {deleted} log files, "
            f"{freed / (1024 * 1024):.1f} MB freed"
        )

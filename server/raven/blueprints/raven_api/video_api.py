"""
Read side of the Video Streams page: the stream list (with live status checked against MediaMTX),
recordings, recording download/delete, and thumbnails. Video has no group isolation: every
authenticated user sees every stream and recording.
"""

import os

import bleach
import requests
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request, send_from_directory
from flask_babel import gettext
from flask_security import auth_required, current_user

from raven.blueprints.raven_api.api import paginate, search
from raven.extensions import db, logger
from raven.group_isolation import is_admin
from raven.models.VideoRecording import VideoRecording
from raven.models.VideoStream import VideoStream
from raven.recordings import contained_recording_path

video_api_blueprint = Blueprint("video_api_blueprint", __name__)

SYNC_TIMEOUT = 2


def sync_ready_with_mediamtx() -> None:
    """Corrects each stream's ready flag (and web copy) from MediaMTX's own path list.

    A notready webhook lost while Raven was down would otherwise leave a stream showing as live.
    Changes nothing when MediaMTX can't be asked."""
    if not app.config.get("RAVEN_MEDIAMTX_ENABLE"):
        return

    try:
        response = requests.get(
            app.config.get("RAVEN_MEDIAMTX_API_ADDRESS").rstrip("/") + "/v3/paths/list",
            params={"itemsPerPage": 1000},
            timeout=SYNC_TIMEOUT,
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        live = {
            item.get("name") for item in items if isinstance(item, dict) and (item.get("ready") or item.get("online"))
        }
    except Exception as e:
        logger.debug(f"Could not read the MediaMTX path list: {e}")
        return

    changed = False
    for stream in db.session.query(VideoStream).all():
        is_live = stream.path in live
        if bool(stream.ready) == is_live:
            continue
        stream.ready = is_live
        if not is_live:
            stream.web_path = None
        changed = True
        logger.info(f"Video stream {stream.path} is {'live' if is_live else 'offline'} in MediaMTX")

    if changed:
        db.session.commit()


@video_api_blueprint.route("/api/video_streams")
@auth_required()
def get_video_streams():
    sync_ready_with_mediamtx()
    query = db.session.query(VideoStream)
    for field in ("username", "protocol", "path", "uid"):
        query = search(query, VideoStream, field)
    return paginate(query, VideoStream)


def _invalid_path():
    return jsonify({"success": False, "error": gettext("Please specify a valid path")}), 400


@video_api_blueprint.route("/api/videos/thumbnail")
@auth_required()
def thumbnail():
    path = request.args.get("path")
    if not path:
        return jsonify({"success": False, "error": "Please specify a path"}), 400

    path = os.path.normpath(path.strip("/"))
    if path in ("", ".", "..") or path.startswith("../") or os.path.isabs(path):
        return _invalid_path()

    recording = request.args.get("recording")
    if recording is not None:
        if not recording or os.path.basename(recording) != recording or recording in (".", ".."):
            return _invalid_path()

    folder = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "mediamtx", "recordings", path)
    if recording and os.path.isfile(os.path.join(folder, recording + ".png")):
        return send_from_directory(folder, recording + ".png")
    if os.path.isdir(folder):
        return send_from_directory(folder, "thumbnail.png")
    return _invalid_path()


@video_api_blueprint.route("/api/videos/recordings")
@auth_required()
def video_recordings():
    query = search(db.session.query(VideoRecording), VideoRecording, "path")
    return paginate(query, VideoRecording)


def _not_found():
    return jsonify({"success": False, "error": gettext("Recording not found")}), 404


def _can_delete_recording(recording) -> bool:
    """An administrator, or whoever may manage the recording's stream."""
    from raven.blueprints.raven_api.mediamtx_api import can_manage_stream

    if is_admin(current_user):
        return True
    stream = db.session.get(VideoStream, recording.path) if recording.path else None
    return stream is not None and can_manage_stream(current_user, stream)


@video_api_blueprint.route("/api/videos/recording", methods=["GET", "DELETE", "HEAD"])
@auth_required()
def download_recording():
    recording_id = request.args.get("id")
    if recording_id is None:
        return jsonify({"success": False, "error": "Please specify a recording ID"}), 400

    recording_id = bleach.clean(recording_id)
    if not recording_id.isdigit():
        return (
            jsonify(
                {
                    "success": False,
                    "error": gettext("Invalid recording_id: %(recording_id)s", recording_id=recording_id),
                }
            ),
            400,
        )

    try:
        recording = db.session.get(VideoRecording, int(recording_id))
        if recording is None:
            return _not_found()

        if request.method == "HEAD":
            return "", 200

        contained = contained_recording_path(recording.segment_path)

        if request.method == "DELETE":
            if not _can_delete_recording(recording):
                return (
                    jsonify(
                        {
                            "success": False,
                            "error": gettext("Only the stream's owner or an administrator can delete this recording"),
                        }
                    ),
                    403,
                )
            if contained:
                if os.path.isfile(contained):
                    os.remove(contained)
            else:
                logger.warning(f"Not removing {recording.segment_path!r}: outside the recordings folder")
            db.session.delete(recording)
            db.session.commit()
            return jsonify({"success": True})

        if not contained or not os.path.isfile(contained):
            return _not_found()
        return send_from_directory(os.path.dirname(contained), os.path.basename(contained))
    except Exception as e:
        db.session.rollback()
        logger.error(f"Recording {recording_id}: {e}")
        return _not_found()

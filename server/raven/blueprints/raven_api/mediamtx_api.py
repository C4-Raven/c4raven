"""
MediaMTX integration.

MediaMTX calls Raven through two hooks: a GET webhook for path lifecycle events and recording
segments (/api/mediamtx/webhook, guarded by the shared RAVEN_MEDIAMTX_TOKEN), and its HTTP auth
callback for every publish and read (/api/external_auth, only reachable on localhost). The web UI
creates, changes and deletes MediaMTX paths through the stream add/update/delete routes, which call
MediaMTX's v3 control API.

Some Android video publishers send RTSP carrying a single MPEG-TS track, which MediaMTX can relay over
RTSP but not play in a browser or record. For those, a detached ffmpeg copies the video out to
<path>_raven_web ("web copy"), and the browser links and recordings use that copy.
"""

import datetime
import hmac
import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from urllib.parse import parse_qs, urlparse

import bleach
import requests
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from flask_security import auth_required, current_user
from flask_security.utils import parse_auth_token, verify_password
from sqlalchemy.exc import IntegrityError
from wtforms import BooleanField

from raven.audit import record_audit_event
from raven.extensions import db, logger
from raven.forms.MediaMTXPathConfig import MediaMTXPathConfig
from raven.group_isolation import is_admin
from raven.login_lockout import is_locked
from raven.models.VideoRecording import VideoRecording
from raven.models.VideoStream import VideoStream
from raven.recordings import contained_recording_path

mediamtx_api_blueprint = Blueprint("mediamtx_api_blueprint", __name__)

WEB_COPY_SUFFIX = "_raven_web"

# Seconds to wait for MediaMTX's control API
MEDIAMTX_TIMEOUT = 10
WEB_COPY_TIMEOUT = 5
# Seconds a thumbnail grab or a probe of a finished segment may take
FFMPEG_TIMEOUT = 30

DEFAULT_RTSP_PORT = 8554

# MediaMTX path settings a non-administrator may set, in their canonical spelling. Everything
# else (runOn* commands, recordPath, rpiCamera*) runs or writes things on the server.
USER_PATH_SETTINGS = (
    "name",
    "source",
    "sourceFingerprint",
    "sourceOnDemand",
    "sourceOnDemandStartTimeout",
    "sourceOnDemandCloseAfter",
    "maxReaders",
    "srtReadPassphrase",
    "fallback",
    "record",
    "recordFormat",
    "recordPartDuration",
    "recordSegmentDuration",
    "recordDeleteAfter",
    "publishUser",
    "publishPass",
    "readUser",
    "readPass",
    "overridePublisher",
    "srtPublishPassphrase",
    "rtspTransport",
    "rtspAnyPort",
    "rtspRangeType",
    "rtspRangeStart",
    "sourceRedirect",
)
_USER_PATH_SETTINGS_LOWER = {key.lower(): key for key in USER_PATH_SETTINGS}

# Body keys never applied on top of the stored settings (compared lower-case)
_SKIPPED_BODY_KEYS = ("csrf_token", "sourceondemand", "path")

YOUTUBE_HOSTS = frozenset(("youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"))

# MediaMTX source type -> protocol name. Exact names first, then prefixes.
_EXACT_SOURCE_TYPES = {"hlsSource": "hls", "rpiCameraSource": "rpi_camera"}
_SOURCE_TYPE_PREFIXES = (
    ("rtsp", "rtsp"),
    ("rtmp", "rtmp"),
    ("srt", "srt"),
    ("udp", "udp"),
    ("webRTC", "webrtc"),
)


class _MissingParameter(Exception):
    pass


# --- small helpers ------------------------------------------------------------------------------


def _api(path: str) -> str:
    return app.config.get("RAVEN_MEDIAMTX_API_ADDRESS").rstrip("/") + path


def _clean(value):
    if value is None:
        return None
    return bleach.clean(str(value))


def _arg(name: str) -> str:
    """A sanitised query parameter that the event needs."""
    value = request.args.get(name)
    if value is None:
        raise _MissingParameter(name)
    return bleach.clean(value)


def _server_token() -> str:
    return app.config.get("RAVEN_MEDIAMTX_TOKEN") or ""


def _tokens_match(given, expected) -> bool:
    if not given or not expected or not isinstance(given, str):
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _load_settings(stream) -> dict:
    try:
        settings = json.loads(stream.mediamtx_settings or "")
    except (TypeError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def _error_text(response) -> str:
    try:
        return response.json().get("error")
    except Exception:
        return getattr(response, "text", "")


def _request_hostname() -> str:
    return urlparse(request.host_url).hostname or request.host


def _port(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_RTSP_PORT


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _protocol_or_none(source_type):
    if not isinstance(source_type, str):
        return None
    if source_type in _EXACT_SOURCE_TYPES:
        return _EXACT_SOURCE_TYPES[source_type]
    for prefix, protocol in _SOURCE_TYPE_PREFIXES:
        if source_type.startswith(prefix):
            return protocol
    return None


def get_stream_protocol(source_type: str) -> str:
    return _protocol_or_none(source_type) or "rtsp"


def user_path_setting(key):
    """The canonical spelling of an allow-listed path setting, or None."""
    if not isinstance(key, str):
        return None
    return _USER_PATH_SETTINGS_LOWER.get(key.lower())


def recording_stream_path(path: str) -> str:
    """The stream a path's recordings belong to: a web copy's are filed under its source."""
    if path and path.endswith(WEB_COPY_SUFFIX):
        return path[: -len(WEB_COPY_SUFFIX)]
    return path


def default_path_settings() -> dict:
    """The path config form with no data: booleans false, everything else null."""
    form = MediaMTXPathConfig(formdata=None, meta={"csrf": False})
    return {
        field.name: (False if isinstance(field, BooleanField) else None) for field in form if field.name != "csrf_token"
    }


def _form_settings(form) -> dict:
    return {field.name: field.data for field in form if field.name != "csrf_token"}


def can_manage_stream(user, stream) -> bool:
    """An administrator, anyone for an ownerless stream, or the stream's owner."""
    if is_admin(user):
        return True
    if not stream.username:
        return True
    return getattr(user, "username", None) is not None and stream.username == user.username


def _new_stream(path: str) -> VideoStream:
    stream = VideoStream()
    stream.path = path
    stream.rover_port = -1
    stream.ignore_embedded_klv = False
    stream.buffer_time = None
    stream.network_timeout = 10000
    return stream


def _yt_dlp() -> str:
    beside_python = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
    if os.path.isfile(beside_python) and os.access(beside_python, os.X_OK):
        return beside_python
    return shutil.which("yt-dlp") or "yt-dlp"


def _is_youtube(source: str) -> bool:
    try:
        return urlparse(source).hostname in YOUTUBE_HOSTS
    except ValueError:
        return False


# --- web copy -----------------------------------------------------------------------------------


def set_web_copy_recording(web_path: str, record: bool) -> None:
    """Turns recording of a web copy path on or off. Never raises."""
    body = {"record": record}
    try:
        response = requests.patch(_api(f"/v3/config/paths/patch/{web_path}"), json=body, timeout=WEB_COPY_TIMEOUT)
        if response.status_code == 404:
            response = requests.post(_api(f"/v3/config/paths/add/{web_path}"), json=body, timeout=WEB_COPY_TIMEOUT)
        if response.status_code != 200:
            logger.warning(f"MediaMTX refused record={record} for {web_path}: {response.status_code} {response.text}")
    except Exception as e:
        logger.warning(f"Could not set record={record} for {web_path}: {e}")


def start_web_copy(path: str, rtsp_port, record: bool = False):
    """Starts an ffmpeg copy of an MPEG-TS-over-RTSP stream that browsers can play.

    Returns the copy's path, or None when the stream doesn't need one or ffmpeg can't start."""
    try:
        response = requests.get(_api(f"/v3/paths/get/{path}"), timeout=WEB_COPY_TIMEOUT)
        tracks = response.json().get("tracks")
    except Exception as e:
        logger.warning(f"Could not read the tracks of {path}: {e}")
        return None

    if tracks != ["MPEG-TS"]:
        return None

    web_path = path + WEB_COPY_SUFFIX
    port = int(rtsp_port or DEFAULT_RTSP_PORT)
    token = _server_token()

    # Before ffmpeg starts, so the recording begins with the first frame
    set_web_copy_recording(web_path, record)

    command = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        f"rtsp://127.0.0.1:{port}/{path}?token={token}",
        "-map",
        "0:v",
        "-map",
        "0:a?",
        "-c",
        "copy",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        f"rtsp://127.0.0.1:{port}/{web_path}?token={token}",
    ]
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        logger.error(f"Could not start the web copy of {path}: {e}")
        return None

    logger.info(f"Started a web copy of {path} at {web_path}")
    return web_path


# --- webhook ------------------------------------------------------------------------------------


@mediamtx_api_blueprint.route("/api/mediamtx/webhook")
def mediamtx_webhook():
    token = request.args.get("token")
    if not _tokens_match(_clean(token), _server_token()):
        return jsonify({"success": False, "error": gettext("Invalid token")}), 401

    try:
        event = _arg("event")
        if event == "init":
            return _on_init()
        if event in ("ready", "notready"):
            return _on_ready(event)
        if event == "segment_record":
            return _on_segment_record()
        if event == "segment_record_complete":
            return _on_segment_record_complete()
    except _MissingParameter as e:
        logger.warning(f"MediaMTX webhook without the {e} parameter")
        return jsonify({"success": False, "error": gettext("Missing parameter: %(name)s", name=str(e))}), 400

    # connect, disconnect, read, unread and anything new
    return "", 200


def _on_init():
    if _arg("path") != "startup":
        return "", 200

    for stream in db.session.query(VideoStream).all():
        try:
            requests.post(
                _api(f"/v3/config/paths/add/{stream.path}"),
                json=json.loads(stream.mediamtx_settings),
                timeout=MEDIAMTX_TIMEOUT,
            )
        except Exception as e:
            logger.error(f"Could not add {stream.path} to MediaMTX: {e}")

    items = requests.get(_api("/v3/paths/list"), timeout=MEDIAMTX_TIMEOUT).json().get("items") or []
    rtsp_port = None
    hostname = _request_hostname()
    for item in items:
        name = item.get("name")
        source = item.get("source")
        if not name or source is None or db.session.get(VideoStream, name) is not None:
            continue

        if rtsp_port is None:
            global_config = requests.get(_api("/v3/config/global/get"), timeout=MEDIAMTX_TIMEOUT).json()
            rtsp_port = str(global_config.get("rtspAddress", "")).replace(":", "")
        path_config = requests.get(_api(f"/v3/config/paths/get/{name}"), timeout=MEDIAMTX_TIMEOUT).json()

        stream = _new_stream(name)
        stream.protocol = get_stream_protocol(source.get("type") if isinstance(source, dict) else None)
        stream.port = rtsp_port
        stream.mediamtx_settings = json.dumps(path_config)
        stream.alias = name
        stream.rtsp_reliable = 1
        stream.ready = False
        stream.uid = str(uuid.uuid4())
        stream.username = None
        stream.generate_xml(hostname)
        db.session.add(stream)
        db.session.commit()
        logger.info(f"Added the MediaMTX path {name} as a video stream")

    return "", 200


def _on_ready(event: str):
    path = _arg("path")
    if path.endswith(WEB_COPY_SUFFIX):
        # Raven's own copy of a stream, not a stream of its own
        return "", 200

    rtsp_port = _arg("rtsp_port")
    source_type = _arg("source_type")
    source_id = _arg("source_id")
    ready = event == "ready"

    stream = db.session.get(VideoStream, path)
    if stream is not None:
        stream.ready = ready
        db.session.commit()
        try:
            requests.patch(
                _api(f"/v3/config/paths/patch/{path}"),
                json=_load_settings(stream),
                timeout=MEDIAMTX_TIMEOUT,
            )
        except Exception as e:
            logger.error(f"Could not re-apply the settings of {path}: {e}")
    else:
        stream = _new_stream(path)
        protocol = _protocol_or_none(source_type)
        if protocol:
            stream.protocol = protocol
        stream.port = _port(rtsp_port)
        stream.alias = path
        stream.rtsp_reliable = 1
        stream.ready = ready
        stream.uid = str(uuid.uuid4())
        stream.username = None
        settings = default_path_settings()
        settings["sourceOnDemand"] = source_id is not None
        settings["record"] = False
        stream.mediamtx_settings = json.dumps(settings)
        stream.generate_xml(_request_hostname())
        db.session.add(stream)
        db.session.commit()

    if ready:
        record = bool(_load_settings(stream).get("record"))
        stream.web_path = start_web_copy(path, rtsp_port, record)
    else:
        stream.web_path = None
    db.session.commit()

    if ready:
        _grab_thumbnail(path, stream.port)

    return "", 200


def _grab_thumbnail(path: str, port):
    folder = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "mediamtx", "recordings", path)
    try:
        os.makedirs(folder, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-rtsp_transport",
                "tcp",
                "-i",
                f"rtsp://127.0.0.1:{port or DEFAULT_RTSP_PORT}/{path}?token={_server_token()}",
                "-frames:v",
                "1",
                os.path.join(folder, "thumbnail.png"),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=FFMPEG_TIMEOUT,
            check=True,
        )
    except Exception as e:
        logger.error(f"Could not make a thumbnail of {path}: {e}")


def _checked_segment_path():
    segment_path = request.args.get("segment_path")
    if segment_path is None:
        raise _MissingParameter("segment_path")
    if bleach.clean(segment_path) != segment_path or not contained_recording_path(segment_path):
        logger.warning(f"MediaMTX webhook: refusing segment_path outside the recordings folder: {segment_path!r}")
        return None
    return segment_path


def _on_segment_record():
    segment_path = _checked_segment_path()
    if segment_path is None:
        return "", 400
    path = recording_stream_path(_arg("path"))

    recording = db.session.query(VideoRecording).filter_by(segment_path=segment_path).first()
    if recording is None:
        recording = VideoRecording()
        recording.segment_path = segment_path
        recording.path = path
        recording.in_progress = True
        recording.start_time = _now_utc()
        try:
            with db.session.begin_nested():
                db.session.add(recording)
        except IntegrityError:
            # Inserted meanwhile by a concurrent webhook
            recording = db.session.query(VideoRecording).filter_by(segment_path=segment_path).one()

    recording.segment_path = segment_path
    recording.path = path
    recording.in_progress = True
    db.session.commit()
    return "", 200


def _on_segment_record_complete():
    segment_path = _checked_segment_path()
    if segment_path is None:
        return "", 400
    path = recording_stream_path(_arg("path"))
    now = _now_utc()

    recording = db.session.query(VideoRecording).filter_by(segment_path=segment_path).first()
    if recording is not None:
        recording.in_progress = False
        recording.stop_time = now
        if recording.start_time is not None:
            start = recording.start_time.replace(tzinfo=None)
            recording.duration = (now - start).seconds
    else:
        recording = VideoRecording()
        recording.segment_path = segment_path
        recording.path = path
        recording.in_progress = False
        recording.stop_time = now
        db.session.add(recording)

    _probe_segment(recording, segment_path)
    db.session.commit()
    return "", 200


def _probe_segment(recording, segment_path: str):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                segment_path,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT,
            check=True,
        )
        probe = json.loads(result.stdout)

        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video":
                recording.width = _int_or_none(stream.get("width"))
                recording.height = _int_or_none(stream.get("height"))
                recording.video_bitrate = _int_or_none(stream.get("bit_rate"))
                recording.video_codec = stream.get("codec_name")
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-nostdin",
                        "-loglevel",
                        "error",
                        "-ss",
                        "00:00:01",
                        "-i",
                        segment_path,
                        "-frames:v",
                        "1",
                        segment_path + ".png",
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=FFMPEG_TIMEOUT,
                )
            elif stream.get("codec_type") == "audio":
                recording.audio_codec = stream.get("codec_name")
                recording.audio_samplerate = _int_or_none(stream.get("sample_rate"))
                recording.audio_channels = _int_or_none(stream.get("channels"))
                recording.audio_bitrate = _int_or_none(stream.get("bit_rate"))

        recording.file_size = _int_or_none(probe.get("format", {}).get("size"))
    except Exception as e:
        logger.error(f"Could not probe the recording {segment_path}: {e}")


# --- stream settings ----------------------------------------------------------------------------


def _apply_body(settings: dict, body: dict, admin: bool) -> None:
    for key, value in body.items():
        if not isinstance(key, str) or key.lower() in _SKIPPED_BODY_KEYS:
            continue

        if admin:
            name = bleach.clean(key)
        else:
            name = user_path_setting(key)
            if name is None:
                logger.warning(f"{current_user.username} may not set the MediaMTX path setting {key!r}")
                continue

        if name.lower() == "source":
            if not value:
                settings["sourceOnDemand"] = False
            elif isinstance(value, str) and _is_youtube(value):
                settings["source"] = None
                settings["sourceOnDemand"] = False
                settings["runOnDemand"] = (
                    f'ffmpeg -re -i "$({shlex.quote(_yt_dlp())} -g {shlex.quote(value)})" '
                    "-c:v copy -f rtsp rtsp://127.0.0.1:8554/$RTSP_PATH"
                )
            # Other sources arrive through the form on an add
            continue

        if value is None:
            continue
        settings[name] = bleach.clean(value) if isinstance(value, str) else value


@mediamtx_api_blueprint.route("/api/mediamtx/stream/add", methods=["POST"])
@mediamtx_api_blueprint.route("/api/mediamtx/stream/update", methods=["PATCH"])
@auth_required()
def add_update_stream():
    try:
        form = MediaMTXPathConfig()
        if not form.validate():
            return jsonify({"success": False, "errors": form.errors}), 400

        body = request.get_json()
        if not isinstance(body, dict):
            raise ValueError("The request body must be a JSON object")

        path = bleach.clean(body.get("path") or "")
        if not path:
            return jsonify({"success": False, "error": gettext("Please specify a path name")}), 400
        if path.startswith("/"):
            return jsonify({"success": False, "error": gettext("Path cannot begin with a slash")}), 400

        stream = db.session.get(VideoStream, path)
        if stream is not None and not can_manage_stream(current_user, stream):
            return (
                jsonify(
                    {
                        "success": False,
                        "error": gettext("Only the stream's owner or an administrator can change it"),
                    }
                ),
                403,
            )

        updating = request.path.endswith("update")
        if updating and stream is None:
            return jsonify({"success": False, "error": gettext("Path %(path)s not found", path=path)}), 400

        admin = current_user.has_role("administrator")
        new_stream = None
        if stream is None:
            settings = _form_settings(form)
            if not admin:
                defaults = default_path_settings()
                settings = {
                    key: (value if key in USER_PATH_SETTINGS else defaults.get(key)) for key, value in settings.items()
                }
            new_stream = _new_stream(path)
            new_stream.uid = str(uuid.uuid4())
            new_stream.username = current_user.username
            new_stream.rtsp_reliable = 1
            new_stream.mediamtx_settings = json.dumps(settings)
            new_stream.generate_xml(_request_hostname())
            stream = new_stream
        else:
            settings = _load_settings(stream)

        _apply_body(settings, body, admin)

        if updating:
            response = requests.patch(_api(f"/v3/config/paths/patch/{path}"), json=settings, timeout=MEDIAMTX_TIMEOUT)
        else:
            response = requests.post(_api(f"/v3/config/paths/add/{path}"), json=settings, timeout=MEDIAMTX_TIMEOUT)

        if response.status_code != 200:
            if new_stream is None:
                db.session.rollback()
            return jsonify({"success": False, "error": _error_text(response)}), 400

        stream.mediamtx_settings = json.dumps(settings)
        if new_stream is not None:
            db.session.add(new_stream)
        db.session.commit()

        if any(isinstance(key, str) and key.lower() == "record" for key in body) and stream.web_path:
            set_web_copy_recording(stream.web_path, bool(settings.get("record")))

        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to save the stream settings: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@mediamtx_api_blueprint.route("/api/mediamtx/stream/delete", methods=["DELETE"])
@auth_required()
def delete_stream():
    path = bleach.clean(request.args.get("path") or "")
    if not path:
        return jsonify({"success": False, "error": gettext("Please specify a path name")}), 400

    stream = db.session.get(VideoStream, path)
    if stream is None:
        return jsonify({"success": False, "error": gettext("Path %(path)s not found", path=path)}), 400
    if not can_manage_stream(current_user, stream):
        return (
            jsonify(
                {
                    "success": False,
                    "error": gettext("Only the stream's owner or an administrator can delete it"),
                }
            ),
            403,
        )

    try:
        response = requests.delete(_api(f"/v3/config/paths/delete/{path}"), timeout=MEDIAMTX_TIMEOUT)
    except requests.exceptions.RequestException as e:
        logger.error(f"Could not delete {path} from MediaMTX: {e}")
        return jsonify({"success": False, "error": gettext("MediaMTX is not running")}), 500

    # video_recordings.path references the stream without a cascade, so the recordings go first
    for recording in db.session.query(VideoRecording).filter_by(path=path).all():
        contained = contained_recording_path(recording.segment_path)
        if contained:
            try:
                os.remove(contained)
            except OSError:
                pass
        else:
            logger.warning(f"Not removing {recording.segment_path!r}: outside the recordings folder")
        db.session.delete(recording)
    db.session.flush()
    db.session.delete(stream)
    db.session.commit()

    if response.status_code == 404:
        return "", 200
    return response.text, response.status_code


# --- MediaMTX authentication --------------------------------------------------------------------


def _user_from_jwt(token):
    if not token:
        return None
    try:
        from raven.models.user import User

        data = parse_auth_token(token)
        datastore = app.extensions["security"].datastore
        if hasattr(User, "fs_token_uniquifier"):
            user = datastore.find_user(fs_token_uniquifier=data["uid"])
        else:
            user = datastore.find_user(fs_uniquifier=data["uid"])
        if user is not None and user.active and user.verify_auth_token(data):
            return user
    except Exception:
        pass
    return None


def _video_login_failed(username):
    record_audit_event(
        "auth",
        "video_login_failed",
        username=username,
        success=False,
        details="Wrong video stream (RTSP/RTMP/SRT) credentials",
    )


def _user_from_password(username, password):
    """The active user these video credentials belong to, or None.

    A wrong password is audited as video_login_failed, which doesn't count towards the login
    lockout: a camera retrying an old password must not lock its owner out."""
    if not username or not password:
        return None
    if is_locked(username):
        return None

    from raven.models.user import User

    if app.config.get("RAVEN_ENABLE_LDAP"):
        from flask_ldap3_login import AuthenticationResponseStatus

        from raven.blueprints.raven_api.ldap_api import save_user
        from raven.extensions import ldap_manager

        result = ldap_manager.authenticate(username, password)
        if result.status != AuthenticationResponseStatus.success:
            _video_login_failed(username)
            return None
        user = save_user(result.user_dn, result.user_id, result.user_info, result.user_groups)
    else:
        user = app.extensions["security"].datastore.find_user(username=username)
        if user is None:
            user = db.session.query(User).filter_by(username=username).first()
        if user is None or not user.password or not verify_password(password, user.password):
            _video_login_failed(username)
            return None

    if user is None or not user.active:
        return None
    return user


def _register_published_path(path: str, user, body: dict) -> None:
    settings = default_path_settings()
    settings["sourceOnDemand"] = False
    protocol = _clean(body.get("protocol")) or ""

    stream = _new_stream(path)
    uid = _clean(body.get("id"))
    if uid:
        stream.uid = uid
    stream.protocol = protocol
    stream.alias = path.split("/")[-1]
    stream.username = user.username
    stream.mediamtx_settings = json.dumps(settings)
    if protocol in ("rtsp", "rtmp"):
        # Where Raven re-serves it over RTSP, not where it was published
        stream.port = DEFAULT_RTSP_PORT
        stream.rtsp_reliable = 1
    else:
        stream.rtsp_reliable = 0
    stream.generate_xml(_clean(body.get("ip")) or "")

    try:
        db.session.add(stream)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = db.session.get(VideoStream, path)
        if existing is not None:
            settings = _load_settings(existing)

    try:
        response = requests.post(_api(f"/v3/config/paths/add/{path}"), json=settings, timeout=MEDIAMTX_TIMEOUT)
        if response.status_code != 200:
            logger.error(f"MediaMTX refused to add {path}: {_error_text(response)}")
    except Exception as e:
        logger.error(f"Could not add {path} to MediaMTX: {e}")


def _deny(action, protocol, path, ip, who):
    logger.info(f"MediaMTX auth denied: {action} {protocol} {path!r} from {ip} by {who}")
    return "", 401


@mediamtx_api_blueprint.route("/api/external_auth", methods=["POST"])
def external_auth():
    # nginx adds this to everything it proxies: only MediaMTX on localhost may ask
    if request.headers.get("X-Forwarded-For"):
        return "", 401

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    username = body.get("user") or ""
    password = body.get("password") or ""
    ip = body.get("ip") or ""
    action = body.get("action") or ""
    protocol = body.get("protocol") or ""
    path = str(body.get("path") or "").strip("/")
    raw_query = body.get("query")
    query = parse_qs(raw_query if isinstance(raw_query, str) else "", keep_blank_values=True)

    def query_value(name):
        values = query.get(name)
        return values[0] if values else None

    # 1. Raven itself: thumbnails and web copies
    if _tokens_match(query_value("token"), _server_token()):
        return "", 200

    # 2. Local publishers: MediaMTX's runOnDemand ffmpeg, nginx's RTSPS/RTMPS proxies
    if action == "publish" and ip in (app.config.get("RAVEN_IP_WHITELIST") or []):
        return "", 200

    # 3. Who is asking
    jwt_token = query_value("jwt")
    user = _user_from_jwt(jwt_token)
    who = "jwt" if user is not None else None
    if user is None and isinstance(username, str) and isinstance(password, str):
        user = _user_from_password(username, password)
    if user is not None and who is None:
        who = user.username
    who = who or (username if username else "anonymous")

    # 4. Watching: any user may watch any stream; TAK clients read RTSP without credentials
    if action in ("read", "playback"):
        if user is not None or (action == "read" and protocol == "rtsp"):
            return "", 200
        return _deny(action, protocol, path, ip, who)

    # 5. Publishing
    if action == "publish":
        if user is None:
            return _deny(action, protocol, path, ip, who)

        is_copy = path.endswith(WEB_COPY_SUFFIX)
        stream = db.session.get(VideoStream, path)
        if stream is None and is_copy:
            stream = db.session.get(VideoStream, recording_stream_path(path))

        if stream is None:
            if is_copy and not is_admin(user):
                return _deny(action, protocol, path, ip, who)
            _register_published_path(path, user, body)
            return "", 200

        if is_admin(user) or not stream.username or stream.username == user.username:
            return "", 200

        # Some apps publish every phone to live/VIDEO_1, so an idle stream changes hands
        settings = _load_settings(stream)
        if not stream.ready and not is_copy and not settings.get("runOnDemand") and not settings.get("runOnInit"):
            logger.info(f"{user.username} takes over the idle stream {path} from {stream.username}")
            stream.username = user.username
            db.session.commit()
            return "", 200

        return _deny(action, protocol, path, ip, who)

    # 6. api, metrics, pprof
    if user is not None and is_admin(user):
        return "", 200
    return _deny(action, protocol, path, ip, who)

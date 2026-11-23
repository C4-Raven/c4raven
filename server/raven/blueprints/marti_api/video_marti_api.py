"""TAK video feed sharing: the Video Connection Manager XML (/Marti/vcm) and the JSON /Marti/api/video
API, backed by video_streams rows and MediaMTX's path API.

verify_client_cert and requests are looked up through this module at call time (tests replace them).
"""

import json
import re
import uuid
from urllib.parse import urlparse
from xml.etree.ElementTree import fromstring, tostring

import requests
import sqlalchemy.exc
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from sqlalchemy import select

from raven.blueprints.marti_api.marti_api import verify_client_cert
from raven.extensions import db, logger
from raven.forms.MediaMTXPathConfig import MediaMTXPathConfig
from raven.models.VideoRecording import VideoRecording
from raven.models.VideoStream import VideoStream

video_marti_api = Blueprint("video_marti_api", __name__)

_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9_\-.~/]")


def mediamtx_path_from_alias(alias) -> str:
    """A MediaMTX path name for a feed alias, e.g. "Front Gate Cam" -> "Front_Gate_Cam"."""
    if alias is None:
        return ""
    return _UNSAFE_PATH_CHARS.sub("_", str(alias).lstrip("/"))


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


def _hostname():
    return urlparse(request.url_root).hostname


def _mediamtx_address():
    return app.config.get("RAVEN_MEDIAMTX_API_ADDRESS")


def _default_settings(source=None) -> dict:
    data = {"source": source} if source else None
    form = MediaMTXPathConfig(formdata=None, data=data, meta={"csrf": False})
    settings = form.serialize()
    if not source:
        settings["sourceOnDemand"] = False
    return settings


# --- /Marti/vcm ---


def _child_text(root, tag):
    element = root.find(f".//{tag}")
    return element.text if element is not None else None


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@video_marti_api.route("/Marti/vcm", methods=["POST"])
def post_vcm():
    try:
        root = fromstring(request.get_data())
    except BaseException:
        return _error(gettext("Invalid video connection"), 400)

    connections = root if root.tag == "videoConnections" else root.find(".//videoConnections")
    path = _child_text(connections, "path") if connections is not None else None
    if connections is None or not path:
        return _error(gettext("Invalid video connection"), 400)
    if path.startswith("/"):
        path = path[1:]

    uid = _child_text(connections, "uid")
    if uid:
        taken = db.session.execute(
            select(VideoStream).filter(VideoStream.uid == uid, VideoStream.path != path)
        ).scalar()
        if taken is not None:
            return _error(gettext("A different video stream already uses this uid"), 409)

    stream = db.session.get(VideoStream, path)
    new_stream = stream is None
    if new_stream:
        stream = VideoStream()
        stream.path = path
        stream.mediamtx_settings = json.dumps(_default_settings())

    stream.protocol = _child_text(connections, "protocol") or "rtsp"
    stream.alias = _child_text(connections, "alias")
    stream.uid = uid or stream.uid or str(uuid.uuid4())
    stream.port = _int_or_none(_child_text(connections, "port")) or 8554
    stream.rover_port = _int_or_none(_child_text(connections, "roverPort"))
    stream.ignore_embedded_klv = (_child_text(connections, "ignoreEmbeddedKLV") or "").lower() == "true"
    stream.preferred_mac_address = _child_text(connections, "preferredMacAddress")
    stream.preferred_interface_address = _child_text(connections, "preferredInterfaceAddress")
    stream.buffer_time = _int_or_none(_child_text(connections, "buffer"))
    stream.network_timeout = _int_or_none(_child_text(connections, "timeout")) or 10000
    stream.rtsp_reliable = _int_or_none(_child_text(connections, "rtspReliable"))

    feed = connections.find(".//feed")
    if feed is not None:
        # Never store the credentials of an address like rtsp://user:pass@host
        address = feed.find(".//address")
        if address is not None and address.text and "@" in address.text:
            address.text = address.text.rsplit("@", 1)[1]
        stream.xml = tostring(feed).decode()

    try:
        if new_stream:
            db.session.add(stream)
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
        return _error(gettext("Invalid video connection"), 409)
    return "", 200


@video_marti_api.route("/Marti/vcm", methods=["GET"])
def get_vcm():
    try:
        hostname = _hostname()
        itak = "iTAK" in (request.headers.get("User-Agent") or "")
        feeds = []
        for stream in db.session.execute(select(VideoStream)).scalars():
            if not stream.xml:
                continue
            feed = fromstring(stream.xml)
            address = feed.find("address")
            if address is None:
                address = fromstring("<address/>")
                feed.append(address)
            if itak:
                protocol = _child_text(feed, "protocol") or stream.protocol or "rtsp"
                port = _child_text(feed, "port") or stream.port
                feed_path = _child_text(feed, "path") or stream.path or ""
                if not feed_path.startswith("/"):
                    feed_path = f"/{feed_path}"
                address.text = f"{protocol}://{hostname}:{port}{feed_path}"
            else:
                address.text = hostname
            feeds.append(tostring(feed).decode())
        return "<videoConnections>" + "".join(feeds) + "</videoConnections>", 200
    except BaseException as e:
        logger.error(f"Failed to list video connections: {e}")
        return "", 500


# --- /Marti/api/video ---


def _cert_username():
    """The CN of the caller's client cert, or None"""
    cert = verify_client_cert()
    if not cert:
        return None
    return cert.get_subject().commonName


def _find_user(username):
    return app.security.datastore.find_user(username=username)


@video_marti_api.route("/Marti/api/video", methods=["GET"])
def get_videos():
    username = _cert_username()
    if not username:
        return _error(gettext("Invalid Certificate"), 400)
    user = _find_user(username)
    if user is None:
        return _error(gettext("User %(username)s not found", username=username), 401)

    streams = db.session.execute(select(VideoStream)).scalars()
    return jsonify({"videoConnections": [stream.to_marti_json(user) for stream in streams]})


def _protocol_of(url) -> str:
    scheme = urlparse(url or "").scheme
    if scheme in ("http", "https"):
        return "hls"
    return scheme or "rtsp"


@video_marti_api.route("/Marti/api/video", methods=["POST"])
def add_videos():
    username = _cert_username()
    if not username:
        return _error(gettext("Invalid Certificate"), 400)
    if _find_user(username) is None:
        return _error(gettext("User %(username)s not found", username=username), 401)

    body = request.get_json(silent=True) or {}
    for connection in body.get("videoConnections") or []:
        if not isinstance(connection, dict):
            continue
        for feed in connection.get("feeds") or []:
            if not isinstance(feed, dict):
                continue
            alias = feed.get("alias") or connection.get("alias")
            uid = feed.get("uuid") or connection.get("uuid")
            path = mediamtx_path_from_alias(alias)

            stream = None
            if uid:
                stream = db.session.execute(select(VideoStream).filter_by(uid=uid)).scalar()
            if stream is None and path:
                stream = db.session.get(VideoStream, path)
            if stream is not None:
                if alias:
                    stream.alias = alias
                    db.session.commit()
                continue
            if not path:
                continue

            source = feed.get("url")
            settings = _default_settings(source)

            stream = VideoStream()
            stream.path = path
            stream.alias = alias
            stream.uid = uid or str(uuid.uuid4())
            stream.protocol = _protocol_of(source)
            stream.mediamtx_settings = json.dumps(settings)
            stream.username = username
            stream.rover_port = -1
            stream.ignore_embedded_klv = False
            stream.buffer_time = None
            stream.rtsp_reliable = 1
            stream.network_timeout = 10000
            stream.generate_xml(_hostname())
            db.session.add(stream)
            db.session.commit()

            try:
                r = requests.post(f"{_mediamtx_address()}/v3/config/paths/add/{path}", json=settings)
                if r.status_code != 200:
                    logger.error(f"MediaMTX refused to add path {path}: {r.status_code} {r.text}")
            except BaseException as e:
                logger.error(f"Failed to add path {path} to MediaMTX: {e}")

    return "", 200


@video_marti_api.route("/Marti/api/video/<uid>", methods=["GET"])
def get_video(uid):
    username = _cert_username()
    if not username:
        return _error(gettext("Invalid Certificate"), 400)

    stream = db.session.execute(select(VideoStream).filter_by(uid=uid)).scalar()
    if stream is None:
        return _error(gettext("Video not found"), 404)

    user = _find_user(username)
    if user is None:
        return _error(gettext("User %(username)s not found", username=username), 401)
    return jsonify(stream.to_marti_json(user))


@video_marti_api.route("/Marti/api/video/<uid>", methods=["DELETE"])
def delete_video(uid):
    if not _cert_username():
        return _error(gettext("Invalid Certificate"), 400)

    stream = db.session.execute(select(VideoStream).filter_by(uid=uid)).scalar()
    if stream is None:
        return _error(gettext("Video stream with uid %(uid)s not found", uid=uid), 404)

    try:
        r = requests.delete(f"{_mediamtx_address()}/v3/config/paths/delete/{stream.path}")
        if r.status_code != 200:
            logger.error(f"MediaMTX refused to delete path {stream.path}: {r.status_code} {r.text}")
    except BaseException as e:
        logger.error(f"Failed to delete path {stream.path} from MediaMTX: {e}")

    db.session.query(VideoRecording).filter(VideoRecording.path == stream.path).delete(synchronize_session=False)
    db.session.delete(stream)
    db.session.commit()
    return "", 200

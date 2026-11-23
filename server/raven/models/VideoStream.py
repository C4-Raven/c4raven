import json
import uuid
from dataclasses import dataclass
from urllib.parse import unquote_plus, urlsplit, urlunsplit
from xml.etree.ElementTree import Element, SubElement, tostring

from flask import has_request_context, request
from sqlalchemy import TEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.audit import is_sensitive_key
from raven.extensions import db
from raven.models.columns import ref, text
from raven.models.serialization import columns

REDACTED = "***"


def redact_source_url(source):
    """The URL with any user:password@ and sensitive query values replaced by ***."""
    if not isinstance(source, str) or not source:
        return source
    try:
        parts = urlsplit(source)
        netloc = parts.netloc
        if "@" in netloc:
            netloc = REDACTED + "@" + netloc.rsplit("@", 1)[1]
        query = parts.query
        if query:
            # Rewrite only the sensitive values; everything else stays byte for byte
            segments = []
            for segment in query.split("&"):
                name, sep, _ = segment.partition("=")
                if sep and is_sensitive_key(unquote_plus(name)):
                    segment = f"{name}={REDACTED}"
                segments.append(segment)
            query = "&".join(segments)
        return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    except Exception:
        return REDACTED


def _viewer_may_see_source(stream) -> bool:
    if not has_request_context():
        return True  # CLI and scheduled jobs
    try:
        from flask_security import current_user

        if not current_user or not current_user.is_authenticated:
            return False
        if current_user.has_role("administrator"):
            return True
        return bool(stream.username) and current_user.username == stream.username
    except Exception:
        return False


def _request_url_parts():
    url = urlsplit(request.url_root)
    port = url.port or (443 if url.scheme == "https" else 80)
    return url.scheme, url.hostname, port


# MediaMTX/TAK feed settings reported as stored
_FEED_SETTINGS = (
    "network_timeout", "uid", "protocol", "path", "buffer_time", "port", "rover_port",
    "rtsp_reliable", "ignore_embedded_klv", "alias", "preferred_mac_address",
    "preferred_interface_address", "username", "ready",
)  # fmt: skip


@dataclass
class VideoStream(db.Model):
    """A video feed: one MediaMTX path and the TAK feed settings clients get for it."""

    __tablename__ = "video_streams"

    path: Mapped[str] = text(primary_key=True)
    protocol: Mapped[str] = text(default="rtsp")
    port: Mapped[int] = mapped_column(default=8554)
    network_timeout: Mapped[int] = mapped_column(default=10000)
    uid: Mapped[str | None] = text(default=lambda: str(uuid.uuid4()))
    buffer_time: Mapped[int | None] = mapped_column()
    rover_port: Mapped[int | None] = mapped_column()
    rtsp_reliable: Mapped[int | None] = mapped_column(default=1)
    ignore_embedded_klv: Mapped[bool | None] = mapped_column()
    alias: Mapped[str | None] = text()
    preferred_mac_address: Mapped[str | None] = text()
    preferred_interface_address: Mapped[str | None] = text()
    username: Mapped[str | None] = text(ref("user.username"))
    xml: Mapped[str | None] = mapped_column(TEXT)
    ready: Mapped[bool] = mapped_column(default=False)
    # The MediaMTX path configuration, as a JSON object string
    mediamtx_settings: Mapped[str] = mapped_column(TEXT, default="")
    # The path an ffmpeg browser-friendly copy publishes to, while one is running
    web_path: Mapped[str | None] = text()
    cot_id: Mapped[int | None] = mapped_column(ref("cot.id"))

    user = relationship("User", back_populates="video_streams")
    cot = relationship("CoT", back_populates="video")
    recordings = relationship("VideoRecording", back_populates="video_stream")

    @property
    def browser_path(self):
        return self.web_path or self.path

    def _settings(self):
        """mediamtx_settings decoded; {} when it is missing, malformed or not an object."""
        try:
            decoded = json.loads(self.mediamtx_settings)
        except (TypeError, ValueError):
            decoded = None
        return decoded if isinstance(decoded, dict) else {}

    def serialize(self):
        return columns(self, _FEED_SETTINGS)

    def to_json(self):
        settings = self._settings()
        source = settings.get("source")
        if not _viewer_may_see_source(self):
            source = redact_source_url(source)

        scheme, host, port = _request_url_parts()
        web_base = f"{scheme}://{host}:{port}"
        out = self.serialize()
        out.update(
            source=source,
            record=settings.get("record", False),
            rtsp_link=f"rtsp://{host}:{self.port}/{self.path}",
            webrtc_link=f"{web_base}/webrtc/{self.browser_path}/",
            hls_link=f"{web_base}/hls/{self.browser_path}/",
            thumbnail=f"{web_base}/api/videos/thumbnail?path={self.path}",
        )
        return out

    def to_marti_json(self, user):
        url = urlsplit(request.url_root)
        host = url.hostname
        if url.scheme == "https" and url.port and url.port != 443:
            host = f"{host}:{url.port}"
        stream_url = f"https://{host}/hls/{self.browser_path}/index.m3u8"

        stream_uuid = self.uid or str(uuid.uuid4())
        alias = self.alias or self.path
        feed = {
            "uuid": stream_uuid,
            "active": True,
            "alias": alias,
            "url": f"{stream_url}?jwt={user.get_auth_token()}",
            "order": 0,
            "macAddress": self.preferred_mac_address,
            "roverPort": str(self.rover_port) if self.rover_port is not None else "-1",
            "ignoreEmbeddedKLV": "true" if self.ignore_embedded_klv else "false",
            "source": stream_url,
            "networkTimeout": (
                str(self.network_timeout) if self.network_timeout is not None else "10000"
            ),
            "bufferTime": str(self.buffer_time) if self.buffer_time is not None else "-1",
            "rtspReliable": str(self.rtsp_reliable) if self.rtsp_reliable is not None else "1",
            "thumbnail": "",
            "classification": "",
            "latitude": "",
            "longitude": "",
            "fov": "",
            "heading": "",
            "range": "",
            "width": 0,
            "height": 0,
            "bitrate": 0,
        }
        return {
            "active": True,
            "alias": alias,
            "thumbnail": "",
            "classification": "",
            "uuid": stream_uuid,
            "feeds": [feed],
        }

    def generate_xml(self, hostname):
        """Set self.xml to this stream's <feed> (as served by /Marti/vcm)."""
        feed = Element("feed")

        def child(tag, value):
            SubElement(feed, tag).text = value

        child("protocol", "rtsp")
        child("alias", self.alias or self.path)
        child("uid", self.uid or str(uuid.uuid4()))
        child("address", hostname)
        child("port", str(self.port or 8554))
        child("roverPort", str(self.rover_port))
        child("ignoreEmbeddedKLV", "true" if self.ignore_embedded_klv else "false")
        child("preferredMacAddress", self.preferred_mac_address or "")
        child("preferredInterfaceAddress", self.preferred_interface_address or "")
        child("path", self.path or self.alias)
        child("buffer", str(self.buffer_time) if self.buffer_time else "")
        child("timeout", str(self.network_timeout or 10000))
        child("rtspReliable", str(self.rtsp_reliable or 1))
        self.xml = tostring(feed).decode("utf-8")

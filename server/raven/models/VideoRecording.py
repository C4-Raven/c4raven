import os
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from flask import request
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import ref, text
from raven.models.serialization import columns, iso_or_none

# Media details probed from the segment file, reported as stored
_MEDIA = (
    "width", "height", "video_codec", "video_bitrate", "audio_codec", "audio_bitrate",
    "audio_samplerate", "audio_channels", "file_size",
)  # fmt: skip


@dataclass
class VideoRecording(db.Model):
    """A recorded segment file belonging to a video stream."""

    __tablename__ = "video_recordings"

    id: Mapped[int] = mapped_column(primary_key=True)
    segment_path: Mapped[str] = text(unique=True)
    path: Mapped[str] = text(ref("video_streams.path"))
    in_progress: Mapped[bool] = mapped_column()
    start_time: Mapped[datetime | None] = mapped_column()
    stop_time: Mapped[datetime | None] = mapped_column()
    duration: Mapped[int | None] = mapped_column()
    width: Mapped[int | None] = mapped_column()
    height: Mapped[int | None] = mapped_column()
    video_codec: Mapped[str | None] = text()
    video_bitrate: Mapped[int | None] = mapped_column()
    audio_codec: Mapped[str | None] = text()
    audio_bitrate: Mapped[int | None] = mapped_column()
    audio_samplerate: Mapped[int | None] = mapped_column()
    audio_channels: Mapped[int | None] = mapped_column()
    file_size: Mapped[int | None] = mapped_column()

    video_stream = relationship("VideoStream", back_populates="recordings")

    def serialize(self):
        return columns(self, ("segment_path", "path", "in_progress"))

    def _thumbnail_url(self, filename):
        root = urlparse(request.url_root)
        port = root.port or {"https": 443}.get(root.scheme, 80)
        query = f"path={self.path}&recording={filename}"
        return f"{root.scheme}://{root.hostname}:{port}/api/videos/thumbnail?{query}"

    def to_json(self):
        filename = os.path.basename(self.segment_path) if self.segment_path else None
        recording = {"id": self.id}
        recording.update(self.serialize())
        recording.update(
            start_time=iso_or_none(self.start_time),
            stop_time=iso_or_none(self.stop_time),
            duration=self.duration,
            filename=filename,
        )
        recording.update(columns(self, _MEDIA))
        recording["thumbnail"] = self._thumbnail_url(filename)
        return recording

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import text
from raven.models.serialization import columns, iso


def _new_uid():
    return str(uuid.uuid4())


@dataclass
class MissionContent(db.Model):
    """A file, identified by its hash, that is part of one or more missions."""

    __tablename__ = "mission_content"

    id: Mapped[int] = mapped_column(primary_key=True)
    keywords: Mapped[list | None] = mapped_column(JSON)
    mime_type: Mapped[str | None] = text()
    filename: Mapped[str | None] = text()
    submission_time: Mapped[datetime | None] = mapped_column()
    submitter: Mapped[str | None] = text()
    # Unique, so the default must be a callable (one value per row, not per process)
    uid: Mapped[str] = text(unique=True, default=_new_uid)
    creator_uid: Mapped[str | None] = text()
    hash: Mapped[str] = text(nullable=False, unique=True)
    size: Mapped[int | None] = mapped_column()
    expiration: Mapped[int | None] = mapped_column()

    mission_changes = relationship("MissionChange", back_populates="content_resource")
    # Every mission holding the file (a list, whatever the name suggests)
    mission = relationship(
        "Mission",
        secondary="mission_content_mission",
        cascade="all, delete",
        back_populates="contents",
    )

    def serialize(self):
        return columns(
            self,
            (
                "keywords", "mime_type", "filename", "submission_time", "submitter",
                "uid", "creator_uid", "hash", "size", "expiration",
            ),
        )  # fmt: skip

    def to_json(self):
        submitted = iso(self.submission_time)
        resource = {"keywords": self.keywords or [], "mimeType": self.mime_type}
        # The file name is reported under both keys
        resource["name"] = resource["filename"] = self.filename
        resource["submissionTime"] = submitted
        resource.update(columns(self, ("submitter", "uid")))
        resource["creatorUid"] = self.creator_uid
        resource.update(columns(self, ("hash", "size", "expiration")))
        return {"data": resource, "timestamp": submitted, "creatorUid": self.creator_uid}

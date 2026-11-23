import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import Element, SubElement

from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import ref, text
from raven.models.serialization import columns, iso

# How long the change notice for a new log entry stays current
_NOTICE_LIFETIME = timedelta(minutes=2)
# A CoT point that means "no location"
_NOWHERE = {"ce": "9999999", "le": "9999999", "hae": "0.0", "lat": "0.0", "lon": "0.0"}


def _new_uid():
    return str(uuid.uuid4())


def _utc_now():
    return datetime.now(timezone.utc)


@dataclass
class MissionLogEntry(db.Model):
    """An entry in a mission's log (journal)."""

    __tablename__ = "mission_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str] = text()
    creator_uid: Mapped[str] = text()
    # Callable defaults, so every new row gets its own value
    entry_uid: Mapped[str] = text(default=_new_uid)
    mission_name: Mapped[str] = text(ref("missions.name"))
    server_time: Mapped[datetime] = mapped_column(default=_utc_now)
    dtg: Mapped[datetime] = mapped_column(default=_utc_now)
    created: Mapped[datetime] = mapped_column(default=_utc_now)
    # Older single-hash column, still consulted by to_json()
    content_hash: Mapped[str | None] = text()
    content_hashes: Mapped[list | None] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)

    mission = relationship("Mission", back_populates="mission_logs")

    def serialize(self):
        raw = columns(self, ("content", "creator_uid", "entry_uid"))
        raw["mission_names"] = self.mission_name  # plural key, single value
        raw.update(columns(self, ("server_time", "dtg", "created", "content_hashes", "keywords")))
        return raw

    def to_json(self):
        entry = {"id": self.entry_uid, "entryUid": self.entry_uid}
        entry.update(
            content=self.content, creatorUid=self.creator_uid, missionNames=[self.mission_name]
        )
        for key, value in (
            ("servertime", self.server_time),
            ("dtg", self.dtg),
            ("created", self.created),
        ):
            entry[key] = iso(value)
        # The hash list is only reported while the older content_hash column is set (observed)
        entry["contentHashes"] = self.content_hashes if self.content_hash else []
        entry["keywords"] = [] if self.keywords is None else self.keywords
        return entry

    def generate_cot(self) -> Element:
        """A t-x-m-c-l mission change notice announcing this entry to subscribers."""
        stale = iso(self.dtg + _NOTICE_LIFETIME)
        header = {
            "how": "h-g-i-g-o",
            "type": "t-x-m-c-l",
            "version": "2.0",
            "uid": str(uuid.uuid4()),
            "start": iso(self.dtg),
            "time": iso(self.server_time),
            "stale": stale,
            "access": "Undefined",
        }
        event = Element("event", header)
        SubElement(event, "point", dict(_NOWHERE))
        mission = {
            "type": "CHANGE",
            "tool": "public",
            "name": self.mission_name,
            "guid": str(self.mission.guid if self.mission else None),
            "authorUid": self.creator_uid,
        }
        SubElement(SubElement(event, "detail"), "mission", mission)
        return event

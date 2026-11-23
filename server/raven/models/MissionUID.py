from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import ref, text
from raven.models.serialization import columns, iso, renamed


@dataclass
class MissionUID(db.Model):
    """A map item (CoT uid) filed in a mission. The uid is the whole key, so an item is in at
    most one mission at a time."""

    __tablename__ = "mission_uids"

    uid: Mapped[str] = text(primary_key=True)
    mission_name: Mapped[str | None] = text(ref("missions.name"))
    timestamp: Mapped[datetime | None] = mapped_column()
    creator_uid: Mapped[str | None] = text()
    cot_type: Mapped[str | None] = text()
    callsign: Mapped[str | None] = text()
    iconset_path: Mapped[str | None] = text()
    color: Mapped[str | None] = text()
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()

    mission = relationship("Mission", back_populates="uids")
    mission_change = relationship("MissionChange", back_populates="uid", uselist=False)

    def serialize(self):
        return columns(
            self,
            (
                "timestamp",
                "creator_uid",
                "cot_type",
                "callsign",
                "iconset_path",
                "color",
                "latitude",
                "longitude",
            ),
        )

    def color_as_int(self) -> int:
        """The stored colour as an int; -1 when absent or not numeric (WinTAK rejects null)."""
        try:
            return int(self.color)
        except (TypeError, ValueError):
            return -1

    def _location(self):
        # Missing coordinates read as 0.0
        lat, lon = self.latitude, self.longitude
        return {"lat": 0.0 if lat is None else lat, "lon": 0.0 if lon is None else lon}

    def to_details_json(self):
        details = renamed(
            self, {"type": "cot_type", "callsign": "callsign", "iconsetPath": "iconset_path"}
        )
        details["color"] = self.color_as_int()
        details["location"] = self._location()
        return details

    def to_json(self):
        return {
            "data": self.uid,
            "timestamp": iso(self.timestamp),
            "creatorUid": self.creator_uid or "",
            "details": self.to_details_json(),
        }

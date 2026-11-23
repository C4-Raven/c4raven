from dataclasses import dataclass

from sqlalchemy.orm import Mapped, mapped_column

from raven.extensions import db
from raven.models.columns import ref, text
from raven.models.serialization import columns


@dataclass
class MissionContentMission(db.Model):
    """Link table: which mission holds which content file."""

    __tablename__ = "mission_content_mission"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_content_id: Mapped[int] = mapped_column(ref("mission_content.id"))
    mission_name: Mapped[str] = text(ref("missions.name"))

    def serialize(self):
        return columns(self, ("mission_content_id", "mission_name"))

    to_json = serialize

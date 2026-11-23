from dataclasses import dataclass

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import ref, text


@dataclass
class GroupMission(db.Model):
    """Link table: the groups a mission is shared with."""

    __tablename__ = "groups_missions"

    mission_name: Mapped[str] = text(ref("missions.name"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ref("groups.id"), primary_key=True)

    mission = relationship("Mission", cascade="all, delete", viewonly=True)
    group = relationship("Group", cascade="all, delete", viewonly=True)

    def serialize(self):
        keys = {"mission_name": self.mission_name, "group_id": self.group_id}
        return keys | {"mission": self.mission.serialize(), "group": self.group.serialize()}

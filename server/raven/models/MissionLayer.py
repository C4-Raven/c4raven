from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.functions import iso8601_string_from_datetime


@dataclass
class MissionLayer(db.Model):
    """A folder in a mission's layer tree. Siblings form a linked list: after is the uid of the
    sibling this one follows, or None for the first."""

    __tablename__ = "mission_layers"

    GROUP = "GROUP"
    UID = "UID"
    CONTENTS = "CONTENTS"
    MAPLAYER = "MAPLAYER"
    ITEM = "ITEM"
    TYPES = (GROUP, UID, CONTENTS, MAPLAYER, ITEM)

    uid: Mapped[str] = mapped_column(String(255), primary_key=True)
    mission_name: Mapped[str] = mapped_column(
        String(255), ForeignKey("missions.name", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_uid: Mapped[str] = mapped_column(
        String(255), ForeignKey("mission_layers.uid", ondelete="CASCADE"), nullable=True
    )
    after: Mapped[str] = mapped_column(String(255), nullable=True)
    # Item uids and content hashes filed in this layer
    uids: Mapped[list] = mapped_column(JSON, nullable=True)
    hashes: Mapped[list] = mapped_column(JSON, nullable=True)
    creator_uid: Mapped[str] = mapped_column(String(255), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    mission = relationship("Mission", back_populates="layers")

    def serialize(self):
        return {
            "uid": self.uid,
            "mission_name": self.mission_name,
            "name": self.name,
            "type": self.type,
            "parent_uid": self.parent_uid,
            "after": self.after,
            "uids": self.uids,
            "hashes": self.hashes,
            "creator_uid": self.creator_uid,
            "create_time": self.create_time,
        }

    def to_json(self, children=None, uids=None, contents=None):
        # Only TAK's MissionLayer fields: strict clients reject anything else
        return {
            "uid": self.uid,
            "name": self.name,
            "type": self.type,
            "parentUid": self.parent_uid,
            "after": self.after,
            "children": children if children is not None else [],
            "uids": uids if uids is not None else [],
            "contents": contents if contents is not None else [],
            "maplayers": [],
        }

    def to_web_json(self, children=None, uids=None, contents=None):
        data = self.to_json(children=children, uids=uids, contents=contents)
        data["creatorUid"] = self.creator_uid or ""
        data["createTime"] = iso8601_string_from_datetime(self.create_time) if self.create_time else ""
        return data

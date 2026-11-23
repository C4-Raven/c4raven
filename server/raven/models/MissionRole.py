from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import ref, text
from raven.models.serialization import iso


@dataclass
class MissionRole(db.Model):
    """A user's/device's role in a mission. Rows store MISSION_READ_ONLY; on the TAK wire that
    role is called MISSION_READONLY_SUBSCRIBER."""

    __tablename__ = "mission_roles"

    MISSION_MANAGE_FEEDS = "MISSION_MANAGE_FEEDS"
    MISSION_SET_PASSWORD = "MISSION_SET_PASSWORD"
    MISSION_WRITE = "MISSION_WRITE"
    MISSION_MANAGE_LAYERS = "MISSION_MANAGE_LAYERS"
    MISSION_UPDATE_GROUPS = "MISSION_UPDATE_GROUPS"
    MISSION_DELETE = "MISSION_DELETE"
    MISSION_SET_ROLE = "MISSION_SET_ROLE"
    MISSION_READ = "MISSION_READ"

    MISSION_OWNER = "MISSION_OWNER"
    MISSION_SUBSCRIBER = "MISSION_SUBSCRIBER"
    MISSION_READ_ONLY = "MISSION_READ_ONLY"
    MISSION_READONLY_SUBSCRIBER = "MISSION_READONLY_SUBSCRIBER"

    OWNER_ROLE = {
        "type": MISSION_OWNER,
        "permissions": [
            MISSION_MANAGE_FEEDS,
            MISSION_SET_PASSWORD,
            MISSION_WRITE,
            MISSION_MANAGE_LAYERS,
            MISSION_UPDATE_GROUPS,
            MISSION_DELETE,
            MISSION_SET_ROLE,
            MISSION_READ,
        ],
    }
    SUBSCRIBER_ROLE = {"type": MISSION_SUBSCRIBER, "permissions": [MISSION_READ, MISSION_WRITE]}
    READ_ONLY_ROLE = {"type": MISSION_READONLY_SUBSCRIBER, "permissions": [MISSION_READ]}

    id: Mapped[int] = mapped_column(primary_key=True)
    clientUid: Mapped[str] = text()
    username: Mapped[str] = text()
    createTime: Mapped[datetime] = mapped_column()
    role_type: Mapped[str] = text()
    mission_name: Mapped[str | None] = text(ref("missions.name"))
    mission_guid: Mapped[str | None] = text()

    mission = relationship("Mission", back_populates="roles")

    @classmethod
    def normalize_role_type(cls, role_type):
        """Stored form of a role name: the wire name of the read-only role maps to the stored
        one, an empty value to None, anything else is kept."""
        if not role_type:
            return None
        return cls.MISSION_READ_ONLY if role_type == cls.MISSION_READONLY_SUBSCRIBER else role_type

    def _member(self):
        return {"clientUid": self.clientUid, "username": self.username}

    def serialize(self):
        return self._member() | {"createTime": self.createTime, "role_type": self.role_type}

    def to_json(self):
        role = {
            self.MISSION_OWNER: self.OWNER_ROLE,
            self.MISSION_SUBSCRIBER: self.SUBSCRIBER_ROLE,
        }.get(self.role_type, self.READ_ONLY_ROLE)
        return self._member() | {"createTime": iso(self.createTime), "role": role}

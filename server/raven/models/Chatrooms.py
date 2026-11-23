from sqlalchemy.orm import Mapped, relationship

from raven.extensions import db
from raven.models.columns import text
from raven.models.GeoChat import GeoChat  # noqa: F401  (registers the geochats target)
from raven.models.serialization import columns, each


class Chatroom(db.Model):
    """A GeoChat room: one per group or team, plus "All Chat Rooms"."""

    __tablename__ = "chatrooms"

    id: Mapped[str] = text(primary_key=True)
    name: Mapped[str] = text()
    group_owner: Mapped[str | None] = text()
    parent: Mapped[str | None] = text()

    team = relationship("Team", back_populates="chatroom")
    geochats = relationship("GeoChat", cascade="all, delete-orphan", back_populates="chatroom")
    chatroom_uid = relationship(
        "ChatroomsUids", cascade="all, delete-orphan", back_populates="chatroom"
    )

    def serialize(self):
        return columns(self, ("name", "group_owner", "parent"))

    def to_json(self):
        room = self.serialize()
        # An empty room reports null rather than []
        room["geochats"] = each(self.geochats) or None
        return room

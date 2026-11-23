from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, ref, text
from raven.models.serialization import columns


class GeoChat(db.Model):
    """A GeoChat message, keyed by the message's own uid."""

    __tablename__ = "geochat"

    uid: Mapped[str] = text(primary_key=True)
    chatroom_id: Mapped[str] = text(ref("chatrooms.id"))
    sender_uid: Mapped[str] = text(owned_by("euds.uid"))
    remarks: Mapped[str] = text()
    timestamp: Mapped[datetime] = mapped_column(index=True)
    point_id: Mapped[int] = mapped_column(ref("points.id"), index=True)
    cot_id: Mapped[int] = mapped_column(ref("cot.id"), index=True)

    eud = relationship("EUD", back_populates="geochats")
    chatroom = relationship("Chatroom", back_populates="geochats")
    cot = relationship("CoT", cascade="all", back_populates="geochat")
    point = relationship("Point", cascade="all", back_populates="geochat", uselist=False)

    _MESSAGE = ("uid", "sender_uid", "remarks", "timestamp")

    def serialize(self):
        return columns(self, self._MESSAGE)

    def to_json(self):
        # The timestamp stays a datetime here; every message has a point
        message = self.serialize()
        message["point"] = self.point.to_json()
        return message

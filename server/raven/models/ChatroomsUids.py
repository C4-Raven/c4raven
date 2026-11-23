from sqlalchemy.orm import Mapped, relationship

from raven.extensions import db
from raven.models.columns import owned_by, ref, text


class ChatroomsUids(db.Model):
    """Membership link: device uid <-> chat room (both columns form the key)."""

    __tablename__ = "chatrooms_uids"

    chatroom_id: Mapped[str] = text(ref("chatrooms.id"), primary_key=True)
    uid: Mapped[str] = text(owned_by("euds.uid"), primary_key=True)

    eud = relationship("EUD", back_populates="chatroom_uid")
    chatroom = relationship("Chatroom", back_populates="chatroom_uid")

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from raven.extensions import db
from raven.functions import iso8601_string_from_datetime


@dataclass
class PendingClearCommand(db.Model):
    """A remote clear-content command queued for a device that was offline when it was issued.
    The EUD handler delivers undelivered rows when the device connects and stamps delivered_at;
    delivered rows stay for audit. Only the intent is stored: the signed CoT is built at delivery,
    since the plugin accepts it only within +/-15 minutes of signing."""

    __tablename__ = "pending_clear_commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eud_uid: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    clearmaps: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    node_id: Mapped[str] = mapped_column(String(255), nullable=True)

    def serialize(self):
        return {
            "id": self.id,
            "eud_uid": self.eud_uid,
            "clearmaps": self.clearmaps,
            "requested_by": self.requested_by,
            "requested_at": iso8601_string_from_datetime(self.requested_at) if self.requested_at else None,
            "delivered_at": iso8601_string_from_datetime(self.delivered_at) if self.delivered_at else None,
            "node_id": self.node_id,
        }

    def to_json(self):
        return self.serialize()

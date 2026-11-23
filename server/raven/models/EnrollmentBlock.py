from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from raven.extensions import db
from raven.functions import iso8601_string_from_datetime


@dataclass
class EnrollmentBlock(db.Model):
    """A device uid (ATAK clientUid) that may not enroll for a new certificate until an admin
    clears the block. Created when an admin revokes a device's certificate. eud_uid is not a
    foreign key on purpose: purging the EUD must not lift the block."""

    __tablename__ = "enrollment_blocks"

    eud_uid: Mapped[str] = mapped_column(String(255), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=True, index=True)
    # normalized hex serial of the revoked certificate that caused the block
    serial_number: Mapped[str] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=True)
    # naive UTC
    blocked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    blocked_by: Mapped[str] = mapped_column(String(255), nullable=True)

    def to_json(self):
        return {
            "eud_uid": self.eud_uid,
            "username": self.username,
            "serial_number": self.serial_number,
            "reason": self.reason,
            "blocked_at": (
                iso8601_string_from_datetime(self.blocked_at) if self.blocked_at else None
            ),
            "blocked_by": self.blocked_by,
        }

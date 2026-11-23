from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db


@dataclass
class DataPackageRecipient(db.Model):
    """Someone a private (user-to-user) data package was addressed to, taken from the dest
    entries of the fileshare CoT. ATAK/WinTAK address by callsign and iTAK by uid, so either
    eud_uid or callsign may be NULL. eud_uid is deliberately not a foreign key: a callsign may
    not belong to a known device yet."""

    __tablename__ = "data_package_recipients"
    __table_args__ = (
        UniqueConstraint(
            "data_package_hash", "eud_uid", "callsign", name="uq_data_package_recipient"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_package_hash: Mapped[str] = mapped_column(
        String(255), ForeignKey("data_packages.hash", ondelete="CASCADE"), nullable=False
    )
    eud_uid: Mapped[str] = mapped_column(String(255), nullable=True)
    callsign: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    data_package = relationship("DataPackage", back_populates="recipients")

    def serialize(self):
        return {
            "data_package_hash": self.data_package_hash,
            "eud_uid": self.eud_uid,
            "callsign": self.callsign,
            "created_at": self.created_at,
        }

    def to_json(self):
        return self.serialize()

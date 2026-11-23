from dataclasses import dataclass

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db

TARGET_KINDS = ("group", "user", "device")


@dataclass
class DeviceProfileTarget(db.Model):
    """Who a device profile set goes to: a group name, a username or an EUD uid. Plain strings,
    not foreign keys, so a profile can name a device that hasn't connected yet and deleting a
    user or group never widens a profile."""

    __tablename__ = "device_profile_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("device_profile_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)

    profile = relationship("DeviceProfileSet", back_populates="targets")

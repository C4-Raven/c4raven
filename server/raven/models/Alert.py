from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, text
from raven.models.serialization import attribute_of, columns, iso, iso_or_none, nested


class Alert(db.Model):
    """An emergency beacon raised by a device (b-a-* CoT types; b-a-o-can cancels one)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = text()
    sender_uid: Mapped[str] = text(owned_by("euds.uid"))
    start_time: Mapped[datetime] = mapped_column(index=True)
    cancel_time: Mapped[datetime | None] = mapped_column()
    alert_type: Mapped[str] = text()
    point_id: Mapped[int | None] = mapped_column(owned_by("points.id"), index=True)
    cot_id: Mapped[int | None] = mapped_column(owned_by("cot.id"))

    eud = relationship("EUD", back_populates="alert")
    point = relationship("Point", back_populates="alert", foreign_keys=[point_id])
    cot = relationship("CoT", back_populates="alert")

    _RAW = ("uid", "sender_uid", "start_time", "cancel_time", "alert_type", "point_id", "cot_id")

    def serialize(self):
        return columns(self, self._RAW)

    def to_json(self):
        body = columns(self, ("uid", "sender_uid", "alert_type"))
        body.update(
            start_time=iso(self.start_time),
            cancel_time=iso_or_none(self.cancel_time),
            point=nested(self.point),
            callsign=attribute_of(self.eud, "callsign"),
        )
        return body

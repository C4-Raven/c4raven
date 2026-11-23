from datetime import datetime

from sqlalchemy import JSON, TEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, ref, text
from raven.models.serialization import columns, iso, nested


def _owned_single(target):
    """A one-to-one child row built from this event, deleted along with it."""
    return relationship(
        target, cascade="all, delete, delete-orphan", back_populates="cot", uselist=False
    )


class CoT(db.Model):
    """A Cursor-on-Target event as received: its XML plus the header fields the server queries
    on. The alert, point, casevac, video, geochat, marker and R&B line rows derived from an event
    are deleted with it."""

    __tablename__ = "cot"

    id: Mapped[int] = mapped_column(primary_key=True)
    how: Mapped[str | None] = text()
    type: Mapped[str | None] = text(index=True)
    uid: Mapped[str | None] = text(index=True)
    sender_callsign: Mapped[str | None] = text()
    sender_device_name: Mapped[str | None] = text()
    sender_uid: Mapped[str | None] = text(owned_by("euds.uid"), index=True)
    recipients: Mapped[dict | None] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(index=True)
    start: Mapped[datetime] = mapped_column()
    stale: Mapped[datetime] = mapped_column()
    xml: Mapped[str] = mapped_column(TEXT)
    mission_name: Mapped[str | None] = text(ref("missions.name"))

    eud = relationship("EUD", back_populates="cots")
    mission = relationship("Mission", back_populates="cots")
    alert = _owned_single("Alert")
    point = _owned_single("Point")
    casevac = _owned_single("CasEvac")
    video = _owned_single("VideoStream")
    geochat = _owned_single("GeoChat")
    marker = _owned_single("Marker")
    rb_line = relationship("RBLine", cascade="all, delete, delete-orphan", back_populates="cot")

    # Header fields shared by both representations
    _HEADER = ("how", "type", "uid", "sender_callsign", "sender_uid", "recipients", "timestamp")
    # Derived rows embedded by to_json()
    _EMBEDDED = ("eud", "alert", "point", "casevac", "video", "geochat")

    def serialize(self):
        out = columns(self, self._HEADER)
        out.update(start=self.start, stale=self.stale, xml=self.xml)
        return out

    def to_json(self):
        out = columns(self, self._HEADER)
        out.update(start=iso(self.start), stale=iso(self.stale), xml=self.xml)
        for name in self._EMBEDDED:
            out[name] = nested(getattr(self, name))
        return out

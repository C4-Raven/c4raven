import uuid
from datetime import datetime

from sqlalchemy import Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, text
from raven.models.serialization import attribute_of, columns, iso

# Position and sensor values copied from a PointForm by from_wtform()
_FORM_VALUES = (
    "latitude", "longitude", "ce", "hae", "le", "course", "speed",
    "location_source", "battery", "timestamp", "azimuth", "fov",
)  # fmt: skip

_REPORTED = (
    "uid", "device_uid", "latitude", "longitude", "ce", "hae", "le", "course", "speed",
    "azimuth", "fov", "location_source", "battery", "timestamp",
)  # fmt: skip


class Point(db.Model):
    """A location fix: a device's own position report, or the position of a map item."""

    __tablename__ = "points"
    __table_args__ = (
        Index("ix_points_latitude_longitude", "latitude", "longitude"),
        Index("ix_points_device_uid_timestamp", "device_uid", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = text()
    device_uid: Mapped[str | None] = text(owned_by("euds.uid"))
    latitude: Mapped[float | None] = mapped_column()
    longitude: Mapped[float | None] = mapped_column()
    ce: Mapped[float | None] = mapped_column()
    hae: Mapped[float | None] = mapped_column()
    le: Mapped[float | None] = mapped_column()
    course: Mapped[float | None] = mapped_column()
    speed: Mapped[float | None] = mapped_column()
    location_source: Mapped[str | None] = text()
    battery: Mapped[float | None] = mapped_column()
    timestamp: Mapped[datetime] = mapped_column(index=True)
    azimuth: Mapped[float | None] = mapped_column()
    fov: Mapped[float | None] = mapped_column()
    cot_id: Mapped[int | None] = mapped_column(owned_by("cot.id"), index=True)

    eud = relationship("EUD", back_populates="points")
    cot = relationship("CoT", back_populates="point")
    geochat = relationship("GeoChat", back_populates="point", uselist=False)
    marker = relationship("Marker", cascade="all, delete", back_populates="point")
    casevac = relationship("CasEvac", cascade="all", back_populates="point")
    alert = relationship("Alert", cascade="all", back_populates="point")
    rb_line = relationship("RBLine", cascade="all", back_populates="point")
    citrap = relationship("CITrap", cascade="all", back_populates="point")

    def from_wtform(self, form):
        """Fill a new point (with a fresh uid) from a submitted PointForm."""
        self.uid = str(uuid.uuid4())
        for name in _FORM_VALUES:
            setattr(self, name, getattr(form, name).data)

    def serialize(self):
        return columns(self, _REPORTED)

    def to_json(self):
        report = self.serialize()
        cot = self.cot
        report.update(
            timestamp=iso(self.timestamp),
            how=attribute_of(cot, "how"),
            type=attribute_of(cot, "type"),
            callsign=attribute_of(self.eud, "callsign"),
        )
        return report

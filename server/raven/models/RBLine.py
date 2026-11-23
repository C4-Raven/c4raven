import math
from dataclasses import dataclass
from datetime import datetime

from pygc import great_circle
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, text
from raven.models.serialization import columns, iso, nested

# Line attributes reported as stored (serialize() and to_json())
_STYLE = ("color", "color_hex", "stroke_weight", "stroke_style", "labels_on")
_ENDPOINT = ("end_latitude", "end_longitude")


@dataclass
class RBLine(db.Model):
    """A range & bearing line drawn on the map (u-rb-a CoT)."""

    __tablename__ = "rb_lines"

    # Index -> label for the *_units / north_ref integer codes
    range_unit_names = ["standard", "metric", "nautical"]
    bearing_unit_names = ["degrees", "mils"]
    north_ref_names = ["true", "magnetic", "grid"]

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_uid: Mapped[str] = text(owned_by("euds.uid"))
    uid: Mapped[str] = text(unique=True)
    timestamp: Mapped[datetime] = mapped_column()
    range: Mapped[float] = mapped_column()
    bearing: Mapped[float] = mapped_column()
    inclination: Mapped[float | None] = mapped_column()
    anchor_uid: Mapped[str | None] = text()
    range_units: Mapped[int | None] = mapped_column()
    bearing_units: Mapped[int | None] = mapped_column()
    north_ref: Mapped[int | None] = mapped_column()
    color: Mapped[int | None] = mapped_column()
    color_hex: Mapped[str | None] = text()
    callsign: Mapped[str | None] = text()
    stroke_color: Mapped[int | None] = mapped_column()
    stroke_weight: Mapped[float | None] = mapped_column()
    stroke_style: Mapped[str | None] = text()
    labels_on: Mapped[bool | None] = mapped_column()
    point_id: Mapped[int | None] = mapped_column(owned_by("points.id"), index=True)
    cot_id: Mapped[int | None] = mapped_column(owned_by("cot.id"))
    end_latitude: Mapped[float | None] = mapped_column()
    end_longitude: Mapped[float | None] = mapped_column()

    eud = relationship("EUD", back_populates="rb_lines")
    cot = relationship("CoT", back_populates="rb_line")
    point = relationship("Point", back_populates="rb_line")

    def color_to_hex(self):
        """color as eight upper-case hex digits (two's complement for negative values)."""
        return f"{self.color & 0xFFFFFFFF:08X}" if self.color else None

    def _azimuth_degrees(self):
        # Unit code 0 is degrees already. Every other code, mils included, is treated as radians:
        # that is how clients have been observed to send it.
        return self.bearing if self.bearing_units == 0 else math.degrees(self.bearing)

    def calc_end_point(self, start_point):
        """The far end of the line from start_point (a Point); range is in metres."""
        return great_circle(
            latitude=start_point.latitude,
            longitude=start_point.longitude,
            azimuth=self._azimuth_degrees(),
            distance=self.range,
        )

    @staticmethod
    def _label(names, code, present):
        return names[code] if present else None

    def serialize(self):
        out = columns(self, ("uid", "timestamp", "range", "inclination", "anchor_uid"))
        out.update(columns(self, ("range_units", "bearing_units", "north_ref")))
        out.update(columns(self, _STYLE + _ENDPOINT))
        return out

    def to_json(self):
        out = columns(
            self,
            (
                "uid",
                "range",
                "inclination",
                "anchor_uid",
                "range_units",
                "bearing_units",
                "north_ref",
            ),
        )
        out["timestamp"] = iso(self.timestamp)
        out["range_unit_name"] = self._label(
            self.range_unit_names, self.range_units, bool(self.range_units)
        )
        # Whether a name is given depends on bearing being non-zero, not on the unit (as observed)
        out["bearing_unit_name"] = self._label(
            self.bearing_unit_names,
            self.bearing_units,
            bool(self.bearing) and self.bearing_units is not None,
        )
        out["north_ref_name"] = self._label(
            self.north_ref_names, self.north_ref, bool(self.north_ref)
        )
        out.update(columns(self, _STYLE))
        out["point"] = nested(self.point)
        out.update(columns(self, _ENDPOINT))
        return out

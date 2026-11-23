from dataclasses import dataclass

from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, ref, text
from raven.models.serialization import columns, iso, nested

# Marker attributes reported as stored, by serialize() and to_json() alike
_ATTRIBUTES = (
    "uid", "affiliation", "battle_dimension", "callsign", "readiness", "argb", "color_hex",
    "iconset_path", "parent_callsign", "relation", "relation_type", "production_time",
    "location_source", "mil_std_2525c",
)  # fmt: skip


@dataclass
class Marker(db.Model):
    """A point item on the map (a-* and similar CoT), with its Point and source CoT."""

    __tablename__ = "markers"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = text(unique=True)
    affiliation: Mapped[str | None] = text()
    battle_dimension: Mapped[str | None] = text()
    point_id: Mapped[int] = mapped_column(owned_by("points.id"), index=True)
    callsign: Mapped[str | None] = text()
    readiness: Mapped[bool | None] = mapped_column()
    argb: Mapped[int | None] = mapped_column()
    color_hex: Mapped[str | None] = text()
    iconset_path: Mapped[str | None] = text()
    parent_callsign: Mapped[str | None] = text()
    production_time: Mapped[str | None] = text()
    relation: Mapped[str | None] = text()
    relation_type: Mapped[str | None] = text()
    location_source: Mapped[str | None] = text()
    # Integer, not the INTEGER the icons table's key is declared with
    icon_id: Mapped[int | None] = mapped_column(Integer, ref("icons.id"))
    parent_uid: Mapped[str | None] = text(owned_by("euds.uid"))
    remarks: Mapped[str | None] = text()
    cot_id: Mapped[int | None] = mapped_column(owned_by("cot.id"), index=True)
    mil_std_2525c: Mapped[str | None] = text()

    eud = relationship("EUD", back_populates="markers")
    cot = relationship("CoT", back_populates="marker")
    icon = relationship("Icon", back_populates="markers")
    point = relationship("Point", cascade="all, delete", back_populates="marker")

    def color_to_hex(self):
        """argb as eight upper-case hex digits (two's complement for negative values)."""
        return f"{self.argb & 0xFFFFFFFF:08X}" if self.argb else None

    def serialize(self):
        return columns(self, _ATTRIBUTES)

    def to_json(self):
        out = columns(self, _ATTRIBUTES)
        out["icon"] = nested(self.icon)
        out["point"] = nested(self.point)
        cot = self.cot
        if cot:
            out.update(type=cot.type, how=cot.how, start=iso(cot.start), stale=iso(cot.stale))
        else:
            out.update(type=None, how=None, start=None, stale=None)
        return out

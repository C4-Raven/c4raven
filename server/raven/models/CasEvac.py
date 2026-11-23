from datetime import datetime, timedelta, timezone
from xml.etree.ElementTree import Element, SubElement

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.functions import iso8601_string_from_datetime
from raven.models.columns import owned_by, text
from raven.models.Icon import Icon

# The 9-line columns, in serialisation order
NINE_LINE_FIELDS = (
    "ambulatory",
    "casevac",
    "child",
    "convenience",
    "enemy",
    "epw",
    "equipment_detail",
    "equipment_none",
    "equipment_other",
    "extraction_equipment",
    "freq",
    "friendlies",
    "hlz_marking",
    "hlz_remarks",
    "hoist",
    "litter",
    "marked_by",
    "medline_remarks",
    "nonus_civilian",
    "nonus_military",
    "obstacles",
    "priority",
    "routine",
    "security",
    "terrain_loose",
    "terrain_other",
    "terrain_other_detail",
    "terrain_detail",
    "terrain_none",
    "terrain_rough",
    "terrain_slope",
    "terrain_slope_dir",
    "title",
    "urgent",
    "urgent_surgical",
    "us_civilian",
    "us_military",
    "ventilator",
    "winds_are_from",
    "zone_prot_selection",
)

# Form fields copied by from_wtforms (everything the form edits; not sender/point/cot)
FORM_FIELDS = ("uid", "timestamp") + NINE_LINE_FIELDS

# _medevac_ attributes, by how they are rendered
_BOOL_ATTRS = (
    "casevac",
    "extraction_equipment",
    "equipment_other",
    "equipment_none",
    "hoist",
    "terrain_loose",
    "terrain_none",
    "terrain_rough",
    "terrain_slope",
    "ventilator",
    "terrain_other_detail",
)
_VALUE_ATTRS = (
    "child",
    "convenience",
    "enemy",
    "friendlies",
    "nonus_military",
    "nonus_civilian",
    "us_civilian",
    "us_military",
    "epw",
    "freq",
    "litter",
    "hlz_marking",
    "security",
    "priority",
    "routine",
    "urgent",
    "urgent_surgical",
    "zone_prot_selection",
)
_NONEMPTY_ATTRS = (
    "equipment_detail",
    "marked_by",
    "hlz_remarks",
    "medline_remarks",
    "terrain_detail",
    "terrain_slope_dir",
    "obstacles",
    "winds_are_from",
)


class CasEvac(db.Model):
    """A CASEVAC/MEDEVAC 9-line request (ATAK _medevac_ detail), from CoT or the web form."""

    __tablename__ = "casevac"

    id: Mapped[int] = mapped_column(primary_key=True)
    sender_uid: Mapped[str] = text(owned_by("euds.uid"))
    uid: Mapped[str] = text(unique=True)
    timestamp: Mapped[datetime] = mapped_column()
    ambulatory: Mapped[int | None] = mapped_column()
    casevac: Mapped[bool | None] = mapped_column()
    child: Mapped[int | None] = mapped_column()
    convenience: Mapped[int | None] = mapped_column()
    enemy: Mapped[str | None] = text()
    epw: Mapped[int | None] = mapped_column()
    equipment_detail: Mapped[str | None] = text()
    equipment_none: Mapped[bool | None] = mapped_column()
    equipment_other: Mapped[bool | None] = mapped_column()
    extraction_equipment: Mapped[bool | None] = mapped_column()
    freq: Mapped[float | None] = mapped_column()
    friendlies: Mapped[str | None] = text()
    hlz_marking: Mapped[int | None] = mapped_column()
    hlz_remarks: Mapped[str | None] = text()
    hoist: Mapped[bool | None] = mapped_column()
    litter: Mapped[int | None] = mapped_column()
    marked_by: Mapped[str | None] = text()
    medline_remarks: Mapped[str | None] = text()
    nonus_civilian: Mapped[int | None] = mapped_column()
    nonus_military: Mapped[int | None] = mapped_column()
    obstacles: Mapped[str | None] = text()
    priority: Mapped[int | None] = mapped_column()
    routine: Mapped[int | None] = mapped_column()
    security: Mapped[int | None] = mapped_column()
    terrain_loose: Mapped[bool | None] = mapped_column()
    terrain_other: Mapped[bool | None] = mapped_column()
    # Free text describing "other" terrain (the web form still submits it as a checkbox)
    terrain_other_detail: Mapped[str | None] = text()
    terrain_detail: Mapped[str | None] = text()
    terrain_none: Mapped[bool | None] = mapped_column()
    terrain_rough: Mapped[bool | None] = mapped_column()
    terrain_slope: Mapped[bool | None] = mapped_column()
    terrain_slope_dir: Mapped[str | None] = text()
    title: Mapped[str] = text()
    urgent: Mapped[int | None] = mapped_column()
    us_civilian: Mapped[int | None] = mapped_column()
    us_military: Mapped[int | None] = mapped_column()
    ventilator: Mapped[bool | None] = mapped_column()
    winds_are_from: Mapped[str | None] = text()
    zone_prot_selection: Mapped[int | None] = mapped_column()
    urgent_surgical: Mapped[str | None] = text()
    point_id: Mapped[int | None] = mapped_column(owned_by("points.id"), index=True)
    cot_id: Mapped[int | None] = mapped_column(owned_by("cot.id"))

    eud = relationship("EUD", back_populates="casevacs")
    cot = relationship("CoT", back_populates="casevac")
    point = relationship("Point", back_populates="casevac")
    zmist = relationship("ZMIST", back_populates="casevac", uselist=False, cascade="all, delete")

    def from_wtforms(self, form):
        for field in FORM_FIELDS:
            setattr(self, field, getattr(form, field).data)

    def serialize(self):
        data = {"sender_uid": self.sender_uid, "uid": self.uid, "timestamp": self.timestamp}
        for field in NINE_LINE_FIELDS:
            data[field] = getattr(self, field)
        data["eud"] = self.eud
        return data

    def to_json(self):
        data = {
            "sender_uid": self.sender_uid,
            "uid": self.uid,
            "timestamp": iso8601_string_from_datetime(self.timestamp),
        }
        for field in NINE_LINE_FIELDS:
            data[field] = getattr(self, field)
        icon = (
            db.session.execute(db.select(Icon).filter_by(filename="red_crs.png")).scalars().first()
        )
        data["zmist"] = self.zmist.serialize() if self.zmist else None
        data["eud"] = self.eud.to_json() if self.eud else None
        data["point"] = self.point.to_json() if self.point else None
        data["icon"] = icon.to_json()
        data["start"] = iso8601_string_from_datetime(self.cot.start) if self.cot else None
        data["stale"] = iso8601_string_from_datetime(self.cot.stale) if self.cot else None
        return data

    def to_cot(self):
        """The request as an unserialised CoT <event>. Needs a request context with a logged-in
        user and a point."""
        from flask_security import current_user

        point_time = self.point.timestamp or datetime.now(timezone.utc).replace(tzinfo=None)
        now = iso8601_string_from_datetime(point_time)
        stale = iso8601_string_from_datetime(point_time + timedelta(days=365))

        event = Element(
            "event",
            {
                "type": "b-r-f-h-c",
                "version": "2.0",
                "how": "h-g-i-g-o",
                "uid": self.uid,
                "time": now,
                "start": now,
                "stale": stale,
            },
        )
        # Location attributes are written even when unset (as "None")
        where = self.point
        location = {"ce": where.ce, "hae": where.hae, "le": where.le}
        location.update(lat=where.latitude, lon=where.longitude)
        SubElement(event, "point", {key: str(value) for key, value in location.items()})

        detail = SubElement(event, "detail")
        SubElement(detail, "status", {"readiness": "false"})
        SubElement(detail, "contact", {"callsign": current_user.username})
        SubElement(
            detail,
            "link",
            {
                "parent_callsign": current_user.username,
                "production_time": now,
                "relation": "p-p",
                "type": "b-r-f-h-c",
                "uid": self.uid,
            },
        )

        medevac = SubElement(detail, "_medevac_", {"title": self.title})
        for field in _BOOL_ATTRS:
            value = getattr(self, field)
            if value is not None:
                medevac.set(field, str(value).lower())
        for field in _VALUE_ATTRS:
            value = getattr(self, field)
            if value is not None:
                medevac.set(field, str(value))
        for field in _NONEMPTY_ATTRS:
            value = getattr(self, field)
            if value:
                medevac.set(field, value)

        card = self.zmist
        if card:
            lines = {letter: getattr(card, letter) or "" for letter in ("i", "m", "s", "t")}
            lines["title"] = card.title or "ZMIST1"
            lines["z"] = card.z or ""
            SubElement(SubElement(medevac, "zMistsMap"), "zMist", lines)

        return event

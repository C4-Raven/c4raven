from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, text
from raven.models.serialization import attribute_of, columns, iso

# Form fields copied as submitted by from_wtf(); value_class is normalised separately
_FORM_FIELDS = (
    "preference_key",
    "preference_value",
    "enrollment",
    "connection",
    "tool",
    "active",
    "eud_uid",
)

_STORED = (
    "preference_key", "preference_value", "value_class", "enrollment", "connection", "tool",
    "active", "publish_time", "eud_uid",
)  # fmt: skip


def java_class_name(value) -> str:
    """ "java.lang.Boolean" or "Boolean" -> "class java.lang.Boolean" (as ATAK expects)."""
    simple_name = str(value).rsplit(".", 1)[-1]
    return f"class java.lang.{simple_name}"


@dataclass
class DeviceProfiles(db.Model):
    """One preference pushed to devices (the older per-preference profile; DeviceProfileSet
    replaces it)."""

    __tablename__ = "device_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    preference_key: Mapped[str] = text(nullable=False)
    preference_value: Mapped[str | None] = text()
    value_class: Mapped[str | None] = text()
    enrollment: Mapped[bool | None] = mapped_column(default=True)
    connection: Mapped[bool | None] = mapped_column(default=False)
    tool: Mapped[str | None] = text()
    active: Mapped[bool] = mapped_column(default=True)
    publish_time: Mapped[datetime] = mapped_column()
    eud_uid: Mapped[str | None] = text(owned_by("euds.uid"), default=None)

    eud = relationship("EUD", back_populates="profiles")

    def from_wtf(self, form):
        for name in _FORM_FIELDS:
            setattr(self, name, getattr(form, name).data)
        self.value_class = java_class_name(form.value_class.data)
        self.publish_time = datetime.now(timezone.utc)

    def serialize(self):
        return columns(self, _STORED)

    def to_json(self):
        profile = self.serialize()
        profile["publish_time"] = iso(self.publish_time)
        profile["callsign"] = attribute_of(self.eud, "callsign")
        return profile

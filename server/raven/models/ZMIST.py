from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, text
from raven.models.serialization import columns

# Fields shared by the form, the row and the JSON
_ZMIST_FIELDS = ("i", "m", "s", "t", "title", "z")


class ZMIST(db.Model):
    """The ZMIST casualty card of a CasEvac request."""

    __tablename__ = "zmist"

    id: Mapped[int] = mapped_column(primary_key=True)
    i: Mapped[str | None] = text()  # Injuries
    m: Mapped[str | None] = text()  # Mechanism of injury
    s: Mapped[str | None] = text()  # Signs and symptoms
    t: Mapped[str | None] = text()  # Treatment
    title: Mapped[str | None] = text()
    z: Mapped[int | None] = mapped_column()  # Zap number
    casevac_uid: Mapped[str] = text(owned_by("casevac.uid"))

    casevac = relationship("CasEvac", back_populates="zmist")

    def from_wtform(self, form):
        for name in _ZMIST_FIELDS:
            setattr(self, name, getattr(form, name).data)

    def serialize(self):
        return columns(self, _ZMIST_FIELDS)

    to_json = serialize

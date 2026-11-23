import base64
from dataclasses import dataclass

from sqlalchemy import INTEGER, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import text
from raven.models.serialization import columns


def _png_data_uri(data):
    """A data: URI for PNG bytes; None when there are none."""
    return f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}" if data else None


@dataclass
class Icon(db.Model):
    """A single PNG from an ATAK icon set (iconset.xml entry plus its bitmap)."""

    __tablename__ = "icons"

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    iconset_uid: Mapped[str | None] = text()
    filename: Mapped[str | None] = text()
    groupName: Mapped[str | None] = text()
    type2525b: Mapped[str | None] = text()
    useCnt: Mapped[int | None] = mapped_column()
    bitmap: Mapped[bytes | None] = mapped_column(LargeBinary)
    shadow: Mapped[bytes | None] = mapped_column(LargeBinary)

    markers = relationship("Marker", back_populates="icon")

    def serialize(self):
        described = columns(self, ("iconset_uid", "filename", "groupName", "type2525b", "useCnt"))
        for image in ("bitmap", "shadow"):
            described[image] = _png_data_uri(getattr(self, image))
        return described

    to_json = serialize


class IconSets(db.Model):
    """An ATAK icon set and its default icons per affiliation."""

    __tablename__ = "iconsets"

    id: Mapped[int] = mapped_column(INTEGER, primary_key=True)
    name: Mapped[str | None] = text()
    uid: Mapped[str | None] = text()
    version: Mapped[int | None] = mapped_column()
    defaultFriendly: Mapped[str | None] = text()
    defaultHostile: Mapped[str | None] = text()
    defaultNeutral: Mapped[str | None] = text()
    defaultUnknown: Mapped[str | None] = text()
    selectedGroup: Mapped[str | None] = text()

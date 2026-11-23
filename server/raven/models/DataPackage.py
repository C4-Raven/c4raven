from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.Certificate import current_certificate
from raven.models.columns import owned_by, ref, text

# Imported for its table: data_packages_groups is the secondary of DataPackage.groups
from raven.models.DataPackageGroup import DataPackageGroup  # noqa: F401
from raven.models.serialization import attribute_of, columns, iso

_DESCRIPTION = ("filename", "hash", "creator_uid")
_OPTIONS = (
    "keywords",
    "mime_type",
    "size",
    "tool",
    "expiration",
    "install_on_enrollment",
    "install_on_connection",
)


@dataclass
class DataPackage(db.Model):
    """An uploaded data package or file, including certificate enrollment packages. hash (the
    SHA-256 of the content) is the identity; filenames repeat."""

    __tablename__ = "data_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = text()
    hash: Mapped[str] = text(unique=True)
    creator_uid: Mapped[str | None] = text(owned_by("euds.uid"))
    submission_time: Mapped[datetime] = mapped_column()
    submission_user: Mapped[int | None] = mapped_column(ref("user.id"))
    keywords: Mapped[str | None] = text()
    mime_type: Mapped[str] = text()
    size: Mapped[int] = mapped_column()
    tool: Mapped[str | None] = text()
    expiration: Mapped[str | None] = text()
    install_on_enrollment: Mapped[bool | None] = mapped_column(default=False)
    install_on_connection: Mapped[bool | None] = mapped_column(default=False)

    eud: Mapped["EUD"] = relationship("EUD", back_populates="data_packages")  # noqa: F821
    # A re-issued byte-identical zip is shared by several certificates
    certificates = relationship(
        "Certificate", back_populates="data_package", order_by="Certificate.id"
    )
    user = relationship("User", back_populates="data_packages")
    recipients = relationship(
        "DataPackageRecipient",
        back_populates="data_package",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # No groups = visible to everyone (raven/group_isolation.py)
    groups = relationship("Group", secondary="data_packages_groups")

    @property
    def certificate(self):
        return current_certificate(self.certificates)

    @property
    def is_private(self):
        return (self.tool or "").strip().lower() == "private"

    @property
    def group_names(self):
        return sorted(group.name for group in self.groups)

    def serialize(self):
        raw = columns(self, _DESCRIPTION)
        raw["submission_time"] = self.submission_time
        raw["submission_user"] = self.submission_user
        raw.update(columns(self, _OPTIONS))
        raw["private"] = self.is_private
        return raw

    def to_json(self, include_eud=True):
        out = columns(self, _DESCRIPTION)
        out["submission_time"] = iso(self.submission_time)
        out["submission_user"] = attribute_of(self.user, "username")
        out.update(columns(self, _OPTIONS))
        # EUD.to_json(False) leaves its own latest package out, so the two can't recurse
        out["eud"] = self.eud.to_json(False) if include_eud and self.eud else None
        out["private"] = self.is_private
        out["groups"] = self.group_names
        return out

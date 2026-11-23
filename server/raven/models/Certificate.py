from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, ref, text
from raven.models.serialization import attribute_of, columns, iso, iso_or_none

# Where the certificate's server connection points, reported as stored
_CONNECTION = ("server_address", "server_port", "truststore_filename", "user_cert_filename")


def current_certificate(certificates):
    """The certificate that counts for a device or package: the newest one that isn't revoked,
    or, when every one is revoked, the newest. None for an empty list."""
    if not certificates:
        return None
    newest_first = sorted(certificates, key=lambda cert: cert.id or 0, reverse=True)
    for cert in newest_first:
        if not cert.revoked_at:
            return cert
    return newest_first[0]


@dataclass
class Certificate(db.Model):
    """A client certificate as issued. Each issuance is its own row; revoked rows stay."""

    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(primary_key=True)
    common_name: Mapped[str] = text()
    eud_uid: Mapped[str | None] = text(owned_by("euds.uid"))
    data_package_id: Mapped[int | None] = mapped_column(ref("data_packages.id"))
    callsign: Mapped[str | None] = text()
    username: Mapped[str | None] = text(ref("user.username"))
    # Server-local wall-clock time, not UTC: status compares it with datetime.now()
    expiration_date: Mapped[datetime] = mapped_column()
    server_address: Mapped[str] = text()
    server_port: Mapped[int] = mapped_column()
    truststore_filename: Mapped[str] = text()
    user_cert_filename: Mapped[str] = text()
    csr: Mapped[str | None] = text()
    # Unused; kept so the column stays mapped
    cert_password: Mapped[str | None] = text()
    # Upper-case hex, no leading zeros (raven.cert_revocation.normalize_serial); NULL on old rows
    serial_number: Mapped[str | None] = text(length=64, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column()
    revocation_reason: Mapped[str | None] = text(length=64)

    user = relationship("User", back_populates="certificate", uselist=False)
    eud = relationship("EUD", cascade="all, delete", back_populates="certificates", uselist=False)
    data_package = relationship("DataPackage", back_populates="certificates", uselist=False)

    @property
    def status(self):
        if self.revoked_at:
            return "revoked"
        expires = self.expiration_date
        return "expired" if expires and expires < datetime.now() else "valid"

    def _revocation_fields(self):
        return {
            "serial_number": self.serial_number,
            "revoked_at": iso_or_none(self.revoked_at),
            "revocation_reason": self.revocation_reason,
        }

    def serialize(self):
        out = {"callsign": self.callsign, "expiration_date": self.expiration_date}
        out.update(columns(self, _CONNECTION))
        out.update(cert_password=self.cert_password, eud_uid=self.eud_uid)
        out.update(self._revocation_fields())
        return out

    def revocation_json(self):
        out = columns(self, ("id", "common_name", "username", "callsign", "eud_uid"))
        out["expiration_date"] = iso_or_none(self.expiration_date)
        out.update(self._revocation_fields())
        out["status"] = self.status
        return out

    def to_json(self):
        package = self.data_package
        out = {"callsign": self.callsign, "expiration_date": iso(self.expiration_date)}
        out.update(columns(self, _CONNECTION))
        out["data_package_filename"] = attribute_of(package, "filename")
        out["data_package_hash"] = attribute_of(package, "hash")
        out["eud_uid"] = self.eud_uid
        out.update(self._revocation_fields())
        return out

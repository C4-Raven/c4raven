from dataclasses import dataclass
from datetime import datetime

from flask import g, has_request_context
from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.Certificate import current_certificate
from raven.models.Chatrooms import Chatroom  # noqa: F401
from raven.models.columns import ref, text
from raven.models.serialization import attribute_of, columns, iso_or_none
from raven.models.Team import Team  # noqa: F401  (needed by migrations/autogenerate)

# Device description columns reported unchanged by both serialize() and to_json()
_DESCRIPTION = ("uid", "callsign", "device", "os", "platform", "version", "phone_number")


def _sent_by_device(target, **options):
    """Rows the device sent; they go when the device row is deleted through the ORM."""
    return relationship(target, cascade="all, delete-orphan", back_populates="eud", **options)


@dataclass
class EUD(db.Model):
    """A connected client: ATAK, iTAK, WinTAK, TAKX, a Meshtastic node, or a device known through
    a federation peer. Deleting it through the ORM deletes everything it sent."""

    __tablename__ = "euds"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = text(nullable=False, unique=True)
    callsign: Mapped[str | None] = text(unique=True)
    device: Mapped[str | None] = text()
    os: Mapped[str | None] = text()
    platform: Mapped[str | None] = text()
    version: Mapped[str | None] = text()
    phone_number: Mapped[int | None] = mapped_column(BigInteger)
    last_event_time: Mapped[datetime | None] = mapped_column()
    last_status: Mapped[str | None] = text()
    user_id: Mapped[int | None] = mapped_column(ref("user.id"))
    team_id: Mapped[int | None] = mapped_column(ref("teams.id"))
    team_role: Mapped[str | None] = text()
    meshtastic_id: Mapped[int | None] = mapped_column(BigInteger)
    meshtastic_macaddr: Mapped[str | None] = text()
    last_meshtastic_publish: Mapped[datetime | None] = mapped_column()
    # Provenance id of the federation peer the device was learned from; NULL for local devices
    federated_from: Mapped[str | None] = text()
    # Naive UTC time anything last arrived from the device (see raven/offline_chat.py)
    last_rx_at: Mapped[datetime | None] = mapped_column()

    user = relationship("User", back_populates="euds")
    team = relationship("Team", back_populates="euds")
    owned_missions = relationship("Mission", back_populates="owner")
    points = _sent_by_device("Point")
    cots = _sent_by_device("CoT")
    casevacs = _sent_by_device("CasEvac")
    geochats = _sent_by_device("GeoChat")
    chatroom_uid = _sent_by_device("ChatroomsUids")
    alert = _sent_by_device("Alert")
    rb_lines = _sent_by_device("RBLine")
    data_packages = _sent_by_device("DataPackage", order_by="DataPackage.id")
    markers = relationship("Marker", cascade="all, delete, delete-orphan", back_populates="eud")
    certificates = relationship(
        "Certificate",
        cascade="all, delete, delete-orphan",
        back_populates="eud",
        order_by="Certificate.id",
    )
    # Left to the database: ON DELETE CASCADE removes these rows
    stats = relationship("EUDStats", back_populates="eud")
    profiles = relationship("DeviceProfiles", back_populates="eud")

    @property
    def certificate(self):
        return current_certificate(self.certificates)

    @property
    def latest_data_package(self):
        packages = self.data_packages
        return packages[-1] if packages else None

    def federated_from_name(self):
        """Display name of the federation partner in federated_from; None when unknown or on any
        error. The resolver is cached on flask.g for the rest of the request."""
        if not self.federated_from:
            return None
        try:
            from raven import fedhub_names

            lookup = getattr(g, "_fedhub_name_resolver", None)
            if lookup is None:
                lookup = g._fedhub_name_resolver = fedhub_names.resolver()
            return lookup(self.federated_from)
        except Exception:
            return None

    def serialize(self):
        out = columns(self, _DESCRIPTION)
        out.update(
            columns(
                self,
                (
                    "last_event_time",
                    "last_status",
                    "user_id",
                    "team_id",
                    "team_role",
                    "federated_from",
                ),
            )
        )
        return out

    def to_json(self, include_data_packages=True):
        package = self.latest_data_package if include_data_packages else None
        enrollment = attribute_of(self.certificate, "data_package")
        team = self.team

        out = columns(self, _DESCRIPTION)
        out.update(
            last_event_time=iso_or_none(self.last_event_time),
            last_status=self.last_status,
            username=attribute_of(self.user, "username"),
            last_point=None,
            team=attribute_of(team, "name"),
            team_color=team.get_team_color() if team else None,
            team_role=self.team_role,
            federated_from=self.federated_from,
            data_packages=package.to_json(False) if package else None,
            config_hash=attribute_of(enrollment, "hash"),
        )
        # API responses only. Socket.IO pushes from the CoT parser and EudHandler omit the key so
        # a client merging the update keeps the name it already has.
        if has_request_context():
            out["federated_from_name"] = self.federated_from_name()
        return out

import enum
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import false, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import text
from raven.models.GroupUser import GroupUser  # noqa: F401  (secondary table of users)
from raven.models.serialization import columns, each, iso


class GroupTypeEnum(str, enum.Enum):
    SYSTEM = "SYSTEM"
    LDAP = "LDAP"


# Membership directions each default_access value grants
ACCESS_DIRECTIONS = {"both": ("IN", "OUT"), "IN": ("IN",), "OUT": ("OUT",)}


# Suffixes dropped from the name Marti reports, removed one after the other in this order
_ACCESS_SUFFIXES = (re.compile("_read", re.IGNORECASE), re.compile("_write", re.IGNORECASE))


def _utc_now():
    return datetime.now(timezone.utc)


@dataclass
class Group(db.Model):
    """A TAK channel/group plus the policies raven/group_policy.py applies to it. Memberships
    live in GroupUser, one row per direction."""

    __tablename__ = "groups"

    IN = "IN"
    OUT = "OUT"
    SYSTEM = "SYSTEM"
    LDAP = "LDAP"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = text(unique=True)
    distinguishedName: Mapped[str | None] = text()
    created: Mapped[datetime] = mapped_column(default=_utc_now)
    type: Mapped[str] = text()
    bitpos: Mapped[int] = mapped_column()
    description: Mapped[str | None] = text()
    # Which directions ("both", "IN" or "OUT") a new member is given
    default_access: Mapped[str] = text(length=8, default="both", server_default="both")
    auto_join: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Days until a new membership expires; NULL means it never does
    default_membership_days: Mapped[int | None] = mapped_column()
    # Locked: the group can't be deleted and admins can't edit its memberships
    locked: Mapped[bool] = mapped_column(default=False, server_default=false())

    # Read-only views; memberships are changed through the GroupUser / GroupMission rows
    users = relationship(
        "User",
        secondary="groups_users",
        back_populates="groups",
        cascade="all, delete",
        viewonly=True,
    )
    missions = relationship(
        "Mission",
        secondary="groups_missions",
        back_populates="groups",
        cascade="all, delete",
        viewonly=True,
    )

    def __init__(self):
        self.bitpos = self.get_next_bitpos()

    def get_next_bitpos(self):
        """Next free bit position. Bit 2 belongs to __ANON__, so the first group gets 3."""
        highest = db.session.execute(select(func.max(Group.bitpos))).scalar()
        if highest is None:
            return 3
        return highest + 1

    def _settings(self):
        """The group's policy settings, with the fallbacks older rows need."""
        return {
            "description": self.description,
            "default_access": self.default_access or "both",
            "auto_join": bool(self.auto_join),
            "default_membership_days": self.default_membership_days,
            "locked": bool(self.locked),
        }

    def serialize(self):
        group = columns(self, ("name", "distinguishedName", "created", "type", "bitpos"))
        group.update(self._settings())
        return group

    def to_json(self):
        members = self.users
        group = self.serialize()
        group.update(
            id=self.id,
            # The bit position as a binary string
            bitpos=None if self.bitpos is None else f"{self.bitpos:b}",
            users=each(members, "serialize"),
            member_count=len({member.id for member in members}),
            missions=each(self.missions, "serialize"),
        )
        return group

    def _to_marti_json(self, direction):
        name = self.name or ""
        for suffix in _ACCESS_SUFFIXES:
            name = suffix.sub("", name)
        return {
            "name": name,
            "direction": direction,
            "created": iso(self.created).partition("T")[0],
            "type": self.type,
            "bitpos": self.bitpos,
            "active": True,
            "description": self.description or "",
        }

    def to_marti_json_in(self):
        return self._to_marti_json(self.IN)

    def to_marti_json_out(self):
        return self._to_marti_json(self.OUT)

import calendar
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import ref, text


@dataclass
class GroupUser(db.Model):
    """One direction of a user's group membership: IN lets the user send into the group, OUT
    delivers the group's traffic to them. A two-way member has a row for each."""

    __tablename__ = "groups_users"

    user_id: Mapped[int] = mapped_column(ref("user.id"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ref("groups.id"), primary_key=True)
    direction: Mapped[str] = text(primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Naive UTC; once past, the expire_group_memberships job removes the row
    expires_at: Mapped[datetime | None] = mapped_column(index=True)

    user = relationship("User", cascade="all, delete", viewonly=True)
    group = relationship("Group", cascade="all, delete", viewonly=True)

    def expires_at_iso(self):
        expiry = self.expires_at
        return None if expiry is None else expiry.replace(tzinfo=timezone.utc).isoformat()

    def _membership(self):
        return {"direction": self.direction, "active": self.enabled}

    def serialize(self):
        membership = {"user": self.user.serialize(), "group": self.group.serialize()}
        membership.update(self._membership())
        membership["expires_at"] = self.expires_at_iso()
        return membership

    def to_marti_json(self):
        group = self.group
        created = group.created
        marti = {
            "name": group.name,
            # Seconds since the epoch, as the Marti groups API reports it
            "created": calendar.timegm(created.utctimetuple()) if created else None,
            "type": group.type,
            "bitpos": group.bitpos,
        }
        marti.update(self._membership())
        return marti

    def to_json(self):
        membership = {"username": self.user.username, "group_name": self.group.name}
        membership.update(self._membership())
        return membership

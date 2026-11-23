import enum
from dataclasses import dataclass

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, ref, text
from raven.models.MissionRole import MissionRole
from raven.models.serialization import columns, iso


class InvitationTypeEnum(str, enum.Enum):
    clientUid = "clientUid"
    callsign = "callsign"
    userName = "userName"
    group = "group"
    team = "team"


# Invitee columns, most specific first: the first one set names who was invited
_INVITEE_COLUMNS = ("client_uid", "callsign", "username", "group_name", "team_name")


@dataclass
class MissionInvitation(db.Model):
    """A pending mission invitation for a device uid, callsign, user, group or team."""

    __tablename__ = "mission_invitations"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_name: Mapped[str | None] = text(ref("missions.name"))
    mission_guid: Mapped[str | None] = text()
    client_uid: Mapped[str | None] = text(owned_by("euds.uid"))
    callsign: Mapped[str | None] = text(owned_by("euds.callsign"))
    username: Mapped[str | None] = text(ref("user.username"))
    group_name: Mapped[str | None] = text()
    team_name: Mapped[str | None] = text(ref("teams.name"))
    creator_uid: Mapped[str | None] = text()
    role: Mapped[str | None] = text()
    type: Mapped[str | None] = text(default="callsign")

    mission = relationship("Mission", back_populates="invitations")
    user = relationship("User", back_populates="mission_invitations")
    team = relationship("Team", back_populates="mission_invitations")
    eud_uid = relationship("EUD", foreign_keys=[client_uid], uselist=False)
    eud_callsign = relationship("EUD", foreign_keys=[callsign], uselist=False)

    def serialize(self):
        invitation = columns(self, ("mission_name", "mission_guid"))
        invitation.update(columns(self, _INVITEE_COLUMNS))
        invitation.update(columns(self, ("creator_uid", "role", "type")))
        return invitation

    to_json = serialize

    def _marti_role(self):
        by_stored_name = {
            MissionRole.MISSION_OWNER: MissionRole.OWNER_ROLE,
            MissionRole.MISSION_READ_ONLY: MissionRole.READ_ONLY_ROLE,
        }
        return by_stored_name.get(self.role, MissionRole.SUBSCRIBER_ROLE)

    def _invitee(self):
        value = None
        for column in _INVITEE_COLUMNS:
            value = getattr(self, column)
            if value:
                break
        return value

    def to_marti_json(self, token: str = ""):
        invitee = self._invitee()
        return {
            "missionName": self.mission_name,
            "invitee": invitee,
            "role": self._marti_role(),
            "type": self.type,
            "creatorUid": self.creator_uid,
            # Invitations carry no time of their own: report when they were sent
            "createTime": iso(None),
            "token": token,
            "missionGuid": self.mission.guid or self.mission_guid,
        }

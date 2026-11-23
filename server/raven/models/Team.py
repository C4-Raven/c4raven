from dataclasses import dataclass

from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import ref, text
from raven.models.serialization import each, nested

# ATAK team name -> colour used for it in the web UI (White maps to #000000, as deployed)
TEAM_COLORS = {
    "Cyan": "#00FFFF",
    "White": "#000000",
    "Yellow": "#FFFF00",
    "Orange": "#FFA500",
    "Magenta": "#FF00FF",
    "Red": "#FF0000",
    "Maroon": "#800000",
    "Purple": "#800080",
    "Dark Blue": "#00008B",
    "Blue": "#0000FF",
    "Teal": "#008080",
    "Green": "#00FF00",
    "Dark Green": "#228B22",
    "Brown": "#964B00",
    "Gray": "#808080",
}


@dataclass
class Team(db.Model):
    """One of ATAK's colour teams, with its chat room."""

    __tablename__ = "teams"

    colors = TEAM_COLORS

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = text(unique=True)
    chatroom_id: Mapped[str | None] = text(ref("chatrooms.id"))

    chatroom = relationship("Chatroom", back_populates="team")
    euds = relationship("EUD", back_populates="team")
    mission_invitations = relationship("MissionInvitation", back_populates="team")

    def get_team_color(self):
        return self.colors[self.name]

    def serialize(self):
        return {"name": self.name}

    def to_json(self):
        members = self.euds
        team = self.serialize()
        team["chatroom"] = nested(self.chatroom)
        team["euds"] = each(members) if members else None
        team["color"] = self.get_team_color()
        return team

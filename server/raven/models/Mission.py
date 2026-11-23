from dataclasses import dataclass
from datetime import datetime

from flask import current_app, request
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.models.columns import owned_by, text
from raven.models.MissionRole import MissionRole
from raven.models.serialization import columns, each, iso, nested

# Columns serialize() reports as stored ("group" is added with its __ANON__ fallback)
_STORED = (
    "description", "chat_room", "base_layer", "bbox", "path", "classification", "tool",
    "default_role", "keywords", "creator_uid", "create_time", "external_data", "feeds",
    "map_layers", "invite_only", "expiration", "guid", "password_protected", "password",
)  # fmt: skip

# Marti key -> column, for the optional text fields reported as "" when empty
_TEXT_OR_BLANK = {
    "description": "description",
    "chatRoom": "chat_room",
    "baseLayer": "base_layer",
    "bbox": "bbox",
    "path": "path",
    "classification": "classification",
}


@dataclass
class Mission(db.Model):
    """A TAK Server style data sync mission, keyed by name."""

    __tablename__ = "missions"

    # <mission type="..."> values of the Marti mission notifications
    INVITE = "INVITE"
    DELETE = "DELETE"
    CREATE = "CREATE"
    CHANGE = "CHANGE"

    name: Mapped[str] = text(primary_key=True)
    description: Mapped[str | None] = text()
    chat_room: Mapped[str | None] = text()
    base_layer: Mapped[str | None] = text()
    bbox: Mapped[str | None] = text()
    path: Mapped[str | None] = text()
    classification: Mapped[str | None] = text()
    tool: Mapped[str | None] = text()
    group: Mapped[str | None] = text()
    default_role: Mapped[str | None] = text()
    keywords: Mapped[list | None] = mapped_column(JSON)
    creator_uid: Mapped[str | None] = text(owned_by("euds.uid"))
    create_time: Mapped[datetime | None] = mapped_column()
    # Older JSON copies; rows in mission_external_data / mission_feeds win when there are any
    external_data: Mapped[list | None] = mapped_column(JSON)
    feeds: Mapped[list | None] = mapped_column(JSON)
    map_layers: Mapped[list | None] = mapped_column(JSON)
    invite_only: Mapped[bool | None] = mapped_column()
    expiration: Mapped[int] = mapped_column(nullable=False, default=-1)
    guid: Mapped[str | None] = text()
    password_protected: Mapped[bool | None] = mapped_column()
    password: Mapped[str | None] = text()

    invitations = relationship(
        "MissionInvitation", cascade="all, delete-orphan", back_populates="mission"
    )
    roles = relationship("MissionRole", cascade="all, delete-orphan", back_populates="mission")
    mission_changes = relationship(
        "MissionChange", cascade="all, delete-orphan", back_populates="mission"
    )
    contents = relationship(
        "MissionContent",
        secondary="mission_content_mission",
        cascade="all, delete",
        back_populates="mission",
    )
    cots = relationship("CoT", back_populates="mission")
    uids = relationship("MissionUID", cascade="all, delete-orphan", back_populates="mission")
    mission_logs = relationship(
        "MissionLogEntry", cascade="all, delete-orphan", back_populates="mission"
    )
    layers = relationship(
        "MissionLayer", cascade="all, delete-orphan", passive_deletes=True, back_populates="mission"
    )
    external_data_items = relationship(
        "MissionExternalData",
        cascade="all, delete-orphan",
        back_populates="mission",
        order_by="MissionExternalData.id",
    )
    feed_items = relationship(
        "MissionFeed",
        cascade="all, delete-orphan",
        back_populates="mission",
        order_by="MissionFeed.id",
    )
    owner = relationship("EUD", back_populates="owned_missions")
    groups = relationship(
        "Group", secondary="groups_missions", viewonly=True, back_populates="missions"
    )

    def serialize(self):
        raw = columns(self, _STORED)
        raw["group"] = self.group or "__ANON__"
        return raw

    def _default_role_json(self):
        role = self.default_role
        if role == MissionRole.MISSION_OWNER:
            return MissionRole.OWNER_ROLE
        if role and role != MissionRole.MISSION_SUBSCRIBER:
            return MissionRole.READ_ONLY_ROLE
        return MissionRole.SUBSCRIBER_ROLE

    def _qr_code(self):
        """Connection string ATAK's mission QR scanner understands."""
        host = request.url_root
        for fragment in ("http://", "https://", "/"):
            host = host.replace(fragment, "")
        config = current_app.config
        streaming = f"{host}:{config.get('RAVEN_SSL_STREAMING_PORT')}:ssl"
        marti = f"{host}-{config.get('RAVEN_MARTI_HTTPS_PORT')}-ssl-{self.name}"
        return f"{streaming},{marti},{self.name}"

    @staticmethod
    def _rows_or_legacy(rows, legacy):
        """to_json() of the item rows when there are any, else the older JSON column (or [])."""
        return each(rows) if rows else legacy or []

    def to_json(self):
        out = {"name": self.name}
        # Optional text fields are reported as "" rather than null
        for key, attribute in _TEXT_OR_BLANK.items():
            out[key] = getattr(self, attribute) or ""
        out.update(
            tool=self.tool or "public",
            defaultRole=self._default_role_json(),
            keywords=self.keywords or [],
            creatorUid=self.creator_uid or "",
            # A missing create_time is reported as the current time
            createTime=iso(self.create_time),
            externalData=self._rows_or_legacy(self.external_data_items, self.external_data),
            feeds=self._rows_or_legacy(self.feed_items, self.feeds),
            mapLayers=self.map_layers or [],
            inviteOnly=bool(self.invite_only),
            expiration=-1 if self.expiration is None else self.expiration,
            guid=self.guid or "",
            uids=each(self.uids),
            contents=each(self.contents),
            passwordProtected=bool(self.password_protected),
            missionChanges=[c.to_json() for c in self.mission_changes if not c.hidden_from_marti()],
            qr_code=self._qr_code(),
            owner=nested(self.owner),
            groups=[{"id": g.id, "name": g.name} for g in self.groups],
        )
        return out

    def to_marti_json(self, logs: bool = False):
        """to_json() with plain group names, plus the mission log when logs is set."""
        marti = self.to_json()
        marti["groups"] = [g.name for g in self.groups]
        if logs:
            marti["logs"] = each(self.mission_logs)
        return marti


# Mapped here too: processes that never import the blueprints (cot_parser, eud_handler, the
# fedhub bridge) still have to configure Mission's relationships to these
from raven.models.MissionExternalData import MissionExternalData  # noqa: E402,F401
from raven.models.MissionFeed import MissionFeed  # noqa: E402,F401
from raven.models.MissionLayer import MissionLayer  # noqa: E402,F401

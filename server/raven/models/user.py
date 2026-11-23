import sqlalchemy.exc
from flask_security.models import fsqla_v3 as fsqla
from sqlalchemy import Boolean, Integer, String, Text, false, true
from sqlalchemy.orm import relationship

from raven.admin_view import parse_areas
from raven.extensions import db
from raven.models.serialization import columns, each

# FsUserMixin needs Flask-Security's tables bound before the class is defined. Callers normally
# do this first; bind here too so that importing this module early can't break it.
if fsqla.FsModels.db is None:
    try:
        fsqla.FsModels.set_db_info(db)
    except sqlalchemy.exc.InvalidRequestError:
        pass

# Mapped here so that standalone processes can configure User's relationships
from raven.models.DataPackageRecipient import DataPackageRecipient  # noqa: F401
from raven.models.Group import Group  # noqa: F401
from raven.models.SupportingDocument import SupportingDocument  # noqa: F401
from raven.models.Token import Token  # noqa: F401
from raven.models.WebAuthn import WebAuthn  # noqa: F401

_ACCOUNT = (
    "id", "username", "active", "site_access", "last_login_at", "current_login_at",
    "last_login_ip", "current_login_ip", "email", "login_count", "cert_validity_days",
)  # fmt: skip

# JSON key -> (relationship, method rendering each item)
_RELATED = {
    "euds": ("euds", "serialize"),
    "video_streams": ("video_streams", "serialize"),
    "roles": ("roles", "serialize"),
    "groups": ("groups", "serialize"),
    "group_memberships": ("group_memberships", "to_json"),
}


class User(db.Model, fsqla.FsUserMixin):
    """A Flask-Security user."""

    __tablename__ = "user"

    # Not required and not unique: many users have no email address
    email = db.Column(String(255), nullable=True, unique=False)
    site_access = db.Column(Boolean, nullable=False, default=True, server_default=true())
    # While set, the user may do nothing through the API but change their password
    force_password_change = db.Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # NULL = the server default (RAVEN_CA_EXPIRATION_TIME)
    cert_validity_days = db.Column(Integer, nullable=True)
    # JSON list of the admin areas a superadmin enabled for an admin_viewer
    admin_view_areas = db.Column(Text, nullable=True)

    video_streams = relationship("VideoStream", back_populates="user")
    euds = relationship("EUD", back_populates="user")
    data_packages = relationship("DataPackage", back_populates="user")
    certificate = relationship("Certificate", back_populates="user")
    mission_invitations = relationship("MissionInvitation", back_populates="user")
    tokens = relationship("Token", back_populates="user")
    supporting_documents = relationship("SupportingDocument", back_populates="uploaded_by")
    groups = relationship("Group", secondary="groups_users", back_populates="users", viewonly=True)
    group_memberships = relationship("GroupUser", cascade="all, delete")

    def serialize(self):
        """The account as the admin UI shows it. Never put an auth token in here."""
        account = columns(self, _ACCOUNT)
        account["admin_view_areas"] = parse_areas(self.admin_view_areas)
        for key, (collection, method) in _RELATED.items():
            account[key] = each(getattr(self, collection), method)
        return account

    def to_json(self):
        return self.serialize()

    # Kept from the former @dataclass (which had no fields of its own): all Users compare equal
    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return True

    __hash__ = None

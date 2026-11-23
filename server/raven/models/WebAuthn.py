from flask_security.models import fsqla_v3 as fsqla

from raven.extensions import db


class WebAuthn(db.Model, fsqla.FsWebAuthnMixin):
    """A WebAuthn (passkey / security key) registration of a user."""

    __tablename__ = "web_authn"

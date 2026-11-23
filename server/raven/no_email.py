"""The server sends no email of any kind.

Flask-Security still needs a mail utility class, so NoEmailMailUtil is one that never sends.
Every Flask-Security feature that needs email (self-registration, confirmation, password
recovery, email sign-in, email 2FA, email change, username recovery, notices) is forced off by
apply_no_email_settings(), whatever config.yml says: old keys such as RAVEN_ENABLE_EMAIL and
MAIL_* are dropped and have no effect.

Users whose 2FA method was "email" are moved to authenticator setup at their next sign-in by
reset_email_two_factor(), which runs at every startup.
"""

import email_validator
from flask_security import MailUtil
from flask_security.mail_util import EmailValidateException
from flask_security.twofactor import CodeTfPlugin
from flask_security.utils import get_message

from raven.extensions import logger

# Config keys from when the server could send email: removed from the loaded config, so a
# config.yml that still has them is harmless
LEGACY_EMAIL_CONFIG_KEYS = ("RAVEN_ENABLE_EMAIL", "SECURITY_TWO_FACTOR_RESCUE_MAIL")
LEGACY_EMAIL_CONFIG_PREFIXES = ("MAIL_", "RAVEN_EMAIL_")

# Forced at startup, after config.yml is loaded
FORCED_SECURITY_SETTINGS = {
    "SECURITY_REGISTERABLE": False,
    "SECURITY_CONFIRMABLE": False,
    "SECURITY_RECOVERABLE": False,
    "SECURITY_PASSWORDLESS": False,
    "SECURITY_UNIFIED_SIGNIN": False,
    "SECURITY_CHANGE_EMAIL": False,
    "SECURITY_USERNAME_RECOVERY": False,
    "SECURITY_TWO_FACTOR_RESCUE_EMAIL": False,
    "SECURITY_SEND_REGISTER_EMAIL": False,
    "SECURITY_SEND_PASSWORD_CHANGE_EMAIL": False,
    "SECURITY_SEND_PASSWORD_RESET_EMAIL": False,
    "SECURITY_SEND_PASSWORD_RESET_NOTICE_EMAIL": False,
    "SECURITY_SEND_USERNAME_CHANGE_EMAIL": False,
}
TWO_FACTOR_METHODS = ["authenticator"]
US_METHODS = ["password", "authenticator"]

# Flask-Security's own 2FA method names that deliver the code by email ("mail" is its old name)
EMAIL_TF_METHODS = ("email", "mail")

# Stored in tf_totp_secret (with tf_primary_method empty) for a user who had email 2FA: they
# must set up an authenticator app before their next sign-in completes. Setting one up (or
# choosing to disable 2FA there) replaces it.
TF_SETUP_REQUIRED = "raven:authenticator-setup-required"

_CODE_TF_PLUGIN = "raven.no_email.RavenCodeTfPlugin"


class NoEmailMailUtil(MailUtil):
    """Flask-Security mail utility that never sends anything."""

    def send_mail(self, template, subject, recipient, sender, body, html, **kwargs):
        logger.debug(f"Email is not supported on this server, not sending '{subject}'")
        return None

    def validate(self, email: str) -> str:
        # Format only: never a DNS lookup from a server that may be offline
        try:
            return email_validator.validate_email(email, check_deliverability=False).normalized
        except ValueError:
            raise EmailValidateException(get_message("INVALID_EMAIL_ADDRESS")[0])


class RavenCodeTfPlugin(CodeTfPlugin):
    """Flask-Security's code (authenticator) 2FA, which also sends a user marked
    TF_SETUP_REQUIRED to authenticator setup when they sign in, even when
    SECURITY_TWO_FACTOR_REQUIRED is off."""

    def get_setup_methods(self, user):
        if needs_authenticator_setup(user):
            # Not set up: Flask-Security's tf_login() then starts "setup_from_login"
            return [("authenticator", "Authenticator app")]
        return super().get_setup_methods(user)


def needs_authenticator_setup(user) -> bool:
    return not getattr(user, "tf_primary_method", None) and (
        getattr(user, "tf_totp_secret", None) == TF_SETUP_REQUIRED
    )


def apply_no_email_settings(config) -> None:
    """Forces every email feature off in a Flask config. Call before Security(app)."""
    for key in list(config.keys()):
        if key in LEGACY_EMAIL_CONFIG_KEYS or key.startswith(LEGACY_EMAIL_CONFIG_PREFIXES):
            config.pop(key, None)

    config.update(FORCED_SECURITY_SETTINGS)
    config["SECURITY_TWO_FACTOR_ENABLED_METHODS"] = list(TWO_FACTOR_METHODS)
    config["SECURITY_US_ENABLED_METHODS"] = list(US_METHODS)

    implementations = dict(
        config.get("SECURITY_TWO_FACTOR_IMPLEMENTATIONS")
        or {"webauthn": "flask_security.webauthn.WebAuthnTfPlugin"}
    )
    implementations["code"] = _CODE_TF_PLUGIN
    config["SECURITY_TWO_FACTOR_IMPLEMENTATIONS"] = implementations

    # Sign in by username (and LDAP when enabled), never by email address
    identity = [
        attr
        for attr in (config.get("SECURITY_USER_IDENTITY_ATTRIBUTES") or [])
        if isinstance(attr, dict) and "email" not in attr
    ]
    if not any("username" in attr for attr in identity):
        from flask_security import uia_username_mapper

        identity.insert(0, {"username": {"mapper": uia_username_mapper, "case_insensitive": True}})
    config["SECURITY_USER_IDENTITY_ATTRIBUTES"] = identity


def reset_email_two_factor() -> int:
    """Moves every user whose 2FA method is email to authenticator setup at next sign-in.
    Idempotent; call in an app context. Returns how many users were changed."""
    from sqlalchemy import select

    from raven.audit import record_audit_event
    from raven.extensions import db
    from raven.models import import_all_models
    from raven.models.user import User

    # Runs early in startup, before anything else has imported every model: querying User
    # with only some models loaded fails to resolve its relationships (Role, ...) and leaves
    # the mappers broken for the rest of the process
    import_all_models()

    users = (
        db.session.execute(select(User).where(User.tf_primary_method.in_(EMAIL_TF_METHODS)))
        .scalars()
        .all()
    )
    if not users:
        logger.info("Email 2FA: no users to move to an authenticator app")
        return 0

    names = []
    for user in users:
        user.tf_primary_method = None
        user.tf_totp_secret = TF_SETUP_REQUIRED
        names.append(user.username)
    db.session.commit()

    logger.warning(
        f"Email 2FA is no longer supported: {len(names)} user(s) must set up an authenticator "
        f"app at their next sign-in: {', '.join(str(n) for n in names)}"
    )
    for name in names:
        record_audit_event(
            "auth",
            "tfa_change",
            username=name,
            details="Email 2FA removed: authenticator app setup required at next sign-in",
        )
    return len(names)

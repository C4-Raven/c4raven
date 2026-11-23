from flask_babel import gettext
from flask_security import PasswordUtil

# Passwords double as RTSP/RTMP basic-auth credentials inside stream URLs
# (rtsp://user:password@host/...), where these characters would end the password early.
_URL_UNSAFE = frozenset("@:")


class PasswordValidator(PasswordUtil):
    """Flask-Security's own password rules, after rejecting characters that break stream URLs."""

    def validate(self, password, is_register, **kwargs):
        if _URL_UNSAFE.intersection(password):
            return [gettext("Passwords should not include @ or : characters")], password
        return super().validate(password, is_register, **kwargs)

import unicodedata

from flask_security import UsernameUtil
from flask_security.utils import get_message

_ALLOWED_PUNCTUATION = ("_", ".")


class UsernameValidator(UsernameUtil):
    """Usernames may hold Unicode letters and numbers, underscores and periods."""

    def __init__(self, app):
        super().__init__(app)

    def check_username(self, username):
        for character in username:
            if character in _ALLOWED_PUNCTUATION:
                continue
            if unicodedata.category(character)[0] not in ("L", "N"):
                return get_message("USERNAME_DISALLOWED_CHARACTERS")[0]
        return None

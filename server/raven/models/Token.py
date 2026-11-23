import hashlib
import json
import os
import time
from dataclasses import dataclass

import jwt
from flask import current_app
from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db, logger

ISSUER = "Raven"
AUDIENCE = "Raven"


def _now_epoch():
    return int(time.time())


def _canonical_hash(claims: dict) -> str:
    # json.dumps defaults (insertion order, ", " / ": " separators, ASCII escapes): existing
    # token_hash values were computed this way
    return hashlib.sha256(json.dumps(claims).encode("utf-8")).hexdigest()


def _key_path(name):
    return os.path.join(current_app.config.get("RAVEN_CA_FOLDER"), "certs", "raven", name)


@dataclass
class Token(db.Model):
    """An enrollment token: a JWT signed with the server key, usable as a password for its user.
    Only the hash of its claims is stored."""

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(255), ForeignKey("user.username"), nullable=True, unique=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    total_uses: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    # Epoch seconds, evaluated per row
    creation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=_now_epoch)
    not_before: Mapped[int] = mapped_column(BigInteger, nullable=True)
    expiration: Mapped[int] = mapped_column(BigInteger, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=True, default=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    user = relationship("User", back_populates="tokens")

    def to_json(self, hash=False) -> dict:
        if self.creation is None:
            # The column default only applies on insert; claims need the value now
            self.creation = _now_epoch()
        claims = {"sub": self.username, "iat": self.creation, "iss": ISSUER, "aud": AUDIENCE}
        if hash:
            if self.max_uses:
                claims["max"] = self.max_uses
            if self.not_before:
                claims["nbf"] = self.not_before
            if self.expiration:
                claims["exp"] = self.expiration
        else:
            claims["max"] = self.max_uses
            claims["nbf"] = self.not_before
            claims["exp"] = self.expiration
        return claims

    def hash_token(self, token: str = None) -> str:
        if token is not None:
            return hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.token_hash = _canonical_hash(self.to_json(True))
        return self.token_hash

    def generate_token(self) -> str | None:
        if not self.username:
            return None
        with open(_key_path("raven.nopass.key"), "rb") as f:
            private_key = f.read()
        return jwt.encode(self.to_json(True), private_key, algorithm="RS256")

    @staticmethod
    def verify_token(token, username, consume=True, check_max=True) -> bool:
        """True if token is a valid, enabled, unexhausted enrollment token for username."""
        if not token or not username:
            return False
        try:
            # Not a JWT at all: checked first, so it needs no key file
            jwt.get_unverified_header(token)
            with open(_key_path("raven.pub"), "rb") as f:
                public_key = f.read()
            claims = jwt.decode(token, public_key, algorithms=["RS256"], audience=AUDIENCE)
        except (
            jwt.InvalidSignatureError,
            jwt.ExpiredSignatureError,
            jwt.ImmatureSignatureError,
            jwt.InvalidAudienceError,
            jwt.MissingRequiredClaimError,
        ) as e:
            logger.warning(f"Rejected enrollment token for {username}: {e}")
            return False
        except jwt.DecodeError:
            # Usually just a mistyped password
            logger.info(f"Credentials for {username} are neither its password nor a token")
            return False
        except Exception as e:
            logger.error(f"Failed to verify enrollment token for {username}: {e}")
            return False

        try:
            # Scoped tokens (e.g. onboarding links) are never enrollment tokens
            if "scope" in claims:
                logger.warning(f"Rejected a scoped token used as a password for {username}")
                return False

            subject = claims.get("sub")
            if not isinstance(subject, str) or subject.lower() != str(username).lower():
                logger.warning(f"Rejected an enrollment token for another user (used for {username})")
                return False

            row = db.session.execute(
                db.select(Token).filter_by(token_hash=_canonical_hash(claims))
            ).scalar_one_or_none()
            if row is None:
                logger.warning(f"Enrollment token for {username} is not known to the server")
                return False
            if not row.username or row.username.lower() != subject.lower():
                logger.warning(f"Enrollment token for {username} does not belong to that user")
                return False
            if row.disabled:
                logger.warning(f"Enrollment token for {username} is disabled")
                return False

            uses = row.total_uses or 0
            if check_max and "max" in claims and uses >= claims["max"]:
                logger.warning(f"Enrollment token for {username} has no uses left")
                return False

            if consume:
                row.total_uses = uses + 1
                db.session.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to verify enrollment token for {username}: {e}")
            return False

from sqlalchemy.orm import Mapped, mapped_column

from raven.extensions import db
from raven.models.columns import text


class Plugins(db.Model):
    """An installed server plugin (by distribution name) and its enabled flag."""

    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = text(unique=True)
    distro: Mapped[str] = text(unique=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Package metadata; unknown until the plugin reports it
    author: Mapped[str | None] = text(default=None)
    version: Mapped[str | None] = text(default=None)

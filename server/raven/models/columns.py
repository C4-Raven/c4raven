"""Column shorthands for the model modules.

Nullability follows the Mapped[...] annotation: ``Mapped[str | None]`` is a NULL-able column,
``Mapped[str]`` a NOT NULL one (unless the column says otherwise). Integer, Float, Boolean and
DateTime columns take their SQL type from the annotation too, so only strings (whose length
matters to the schema), JSON and binary columns name a type.
"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import mapped_column

# Every string column in the schema is VARCHAR(255) unless it says otherwise
DEFAULT_LENGTH = 255


def text(*args, length: int = DEFAULT_LENGTH, **kwargs):
    """A VARCHAR(length) column; extra positional args (foreign keys, ...) pass through."""
    return mapped_column(String(length), *args, **kwargs)


def ref(target: str, *, cascade: bool = False) -> ForeignKey:
    """A foreign key to "table.column", optionally ON DELETE CASCADE."""
    if cascade:
        return ForeignKey(target, ondelete="CASCADE")
    return ForeignKey(target)


def owned_by(target: str) -> ForeignKey:
    """A foreign key whose rows go when the referenced row is deleted (ON DELETE CASCADE)."""
    return ref(target, cascade=True)

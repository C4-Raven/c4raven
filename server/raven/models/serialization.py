"""Small building blocks for the models' serialize() / to_json() dictionaries.

serialize() hands back raw column values (datetimes stay datetimes); to_json() is what the web
UI, the Marti API and Socket.IO emit, so its timestamps are ISO 8601 strings and related rows are
nested through their own to_json().
"""

from raven.functions import iso8601_string_from_datetime


def columns(obj, names) -> dict:
    """{name: obj.name} for each attribute name, in the order given."""
    return {name: getattr(obj, name) for name in names}


def renamed(obj, mapping) -> dict:
    """{key: obj.attribute} for each (key, attribute) pair of mapping."""
    return {key: getattr(obj, attribute) for key, attribute in mapping.items()}


def iso(value) -> str:
    """ISO 8601 text of value. An empty value means *now* (the helper's own rule)."""
    return iso8601_string_from_datetime(value)


def iso_or_none(value):
    """ISO 8601 text of value, or None when there is no value."""
    return iso8601_string_from_datetime(value) if value else None


def nested(related, *args):
    """related.to_json(*args), or None when the relationship is empty."""
    return related.to_json(*args) if related else None


def attribute_of(related, name):
    """related.name, or None when the relationship is empty."""
    return getattr(related, name) if related else None


def each(items, method: str = "to_json") -> list:
    """Every item of a collection rendered with the named method."""
    return [getattr(item, method)() for item in items]

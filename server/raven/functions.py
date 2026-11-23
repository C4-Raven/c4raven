"""CoT, date and size helpers.

Several modules use `from raven.functions import *` and pick up datetime/timezone and friends
through it, so the module-level imports below are part of the interface.
"""

import json  # noqa: F401  (re-exported via star imports)
import math
import re
from datetime import datetime, timezone
from xml.etree.ElementTree import Element, SubElement, tostring  # noqa: F401

from flask import current_app as app  # noqa: F401  (re-exported via star imports)

# TAK timestamps: always UTC, "Z" suffix, with or without a fractional part
TAK_TIME_FRACTIONAL = "%Y-%m-%dT%H:%M:%S.%fZ"
TAK_TIME_WHOLE_SECONDS = "%Y-%m-%dT%H:%M:%SZ"

# For WTForms BooleanField(false_values=...)
false_values = (False, "False", "false", "")

# First match wins, so the longer prefixes come before the shorter ones they start with
_TASKINGS = (
    ("t-x-f", "remarks"),
    ("t-x-s", "state/sync"),
    ("t-s", "required"),
    ("t-z", "cancel"),
    ("t-x-c-c", "commcheck"),
    ("t-x-c-g-d", "dgps"),
    ("t-k-d", "destroy"),
    ("t-k-i", "investigate"),
    ("t-k-t", "target"),
    ("t-k", "strike"),
    ("t-", "tasking"),
)

_AFFILIATIONS = (
    ("a-f-", "friendly"),
    ("a-h-", "hostile"),
    ("a-u-", "unknown"),
    ("a-p-", "pending"),
    ("a-a-", "assumed"),
    ("a-n-", "neutral"),
    ("a-s-", "suspect"),
    ("a-j-", "joker"),
    ("a-k-", "faker"),
)

# "G" is checked before "G-I", so "installation" is never returned (kept for compatibility)
_BATTLE_DIMENSIONS = (
    ("A", "airborne"),
    ("G", "ground"),
    ("G-I", "installation"),
    ("S", "surface/sea"),
    ("U", "subsurface"),
    ("F", "sof"),
    ("Z", "unknown"),
    ("P", "space"),
)

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")


def get_tasking(cot_type):
    for prefix, name in _TASKINGS:
        if re.match(re.escape(prefix), cot_type):
            return name
    return None


def get_affiliation(cot_type):
    if re.match("t-", cot_type):
        return get_tasking(cot_type)
    for prefix, name in _AFFILIATIONS:
        if re.match(re.escape(prefix), cot_type):
            return name
    return None


def get_battle_dimension(cot_type):
    for code, name in _BATTLE_DIMENSIONS:
        if re.match("a-.-" + re.escape(code), cot_type):
            return name
    return None


def cot_type_to_2525c(cot_type):
    parts = cot_type.split("-")
    parts.pop(0)
    symbol = "s" + parts[0] + parts[1] + "-"
    for part in parts[2:]:
        if part.isupper():
            symbol += part.lower()
    return symbol.ljust(10, "-")


def datetime_from_iso8601_string(datetime_string):
    if not datetime_string:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.strptime(datetime_string, TAK_TIME_FRACTIONAL)
    except ValueError:
        parsed = datetime.strptime(datetime_string, TAK_TIME_WHOLE_SECONDS)
    return parsed.replace(tzinfo=timezone.utc)


def iso8601_string_from_datetime(datetime_object=None):
    """Wall-clock fields as they are (no conversion to UTC), 4 fractional digits, then Z."""
    if not datetime_object:
        datetime_object = datetime.now(timezone.utc)
    return datetime_object.strftime(TAK_TIME_FRACTIONAL)[:-3] + "Z"


# Server-wide marker ATAK uses to recognise CoT that has already passed through a TAK server
_SERVER_FLOW_TAG = "TAK-Server-f1a8159ef7804f7a8a32d8efc4b773d0"


def add_unknown_point(event: Element, decimals: bool = False) -> Element:
    """Append the TAK "position unknown" point (0,0 with maximal error) to a CoT event."""
    zero = "0.0" if decimals else "0"
    unknown = "9999999"
    return SubElement(event, "point", lat=zero, lon=zero, hae=zero, ce=unknown, le=unknown)


def generate_delete_cot(uid: str, cot_type: str) -> Element:
    """A t-x-d-d tombstone telling clients to remove the item `uid` (of type `cot_type`) from their maps."""
    stamp = iso8601_string_from_datetime()
    event = Element("event", version="2.0", uid=uid, type="t-x-d-d", how="h-g-i-g-o")
    for field in ("start", "time", "stale"):
        event.set(field, stamp)
    # Position is irrelevant for a delete; send the "unknown" point
    add_unknown_point(event)
    detail = SubElement(event, "detail")
    SubElement(detail, "link", uid=uid, type=cot_type, relation="p-p")
    SubElement(detail, "_flow-tags_").set(_SERVER_FLOW_TAG, iso8601_string_from_datetime())
    return event


def format_bytes(size):
    if size is None or size == 0:
        return "0B"
    if size < 0:
        raise ValueError("format_bytes needs a non-negative size")
    index = int(math.floor(math.log(size, 1024)))
    value = round(size / math.pow(1024, index), 2)
    return f"{value} {_SIZE_UNITS[index]}"


def bytes_to_megabytes(size):
    if not size:
        return 0
    return round(size / 1024**2, 2)


def bytes_to_gigabytes(size):
    if not size:
        return 0
    return round(size / 1024**3, 2)

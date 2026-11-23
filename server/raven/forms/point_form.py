from flask_wtf import FlaskForm
from wtforms import DateTimeField, FloatField, StringField
from wtforms.validators import DataRequired, NumberRange, Optional

# CoT's value for an unknown circular/linear error or height
UNKNOWN = 9999999.0
# ISO 8601 UTC with mandatory fractional seconds
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _coordinate(limit):
    # DataRequired treats 0 as missing, so the equator / prime meridian are rejected
    return FloatField(validators=[DataRequired(), NumberRange(min=-limit, max=limit)])


def _non_negative():
    return FloatField(default=0.0, validators=[NumberRange(min=0)])


def _optional_up_to(maximum):
    return FloatField(default=None, validators=[Optional(), NumberRange(min=0, max=maximum)])


class PointForm(FlaskForm):
    """A position report submitted through the API."""

    latitude = _coordinate(90)
    longitude = _coordinate(180)
    ce = FloatField(default=UNKNOWN)
    hae = FloatField(default=UNKNOWN)
    le = FloatField(default=UNKNOWN)
    course = _non_negative()
    speed = _non_negative()
    location_source = StringField()
    battery = _optional_up_to(100)
    timestamp = DateTimeField(validators=[DataRequired()], format=TIMESTAMP_FORMAT)
    azimuth = _optional_up_to(360)
    fov = _optional_up_to(360)

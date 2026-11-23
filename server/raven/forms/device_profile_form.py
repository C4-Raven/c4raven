from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import Optional

from raven.forms.fields import add_fields, flag


class DeviceProfileForm(FlaskForm):
    """One device preference and when it is pushed (enrollment / connection)."""


add_fields(
    DeviceProfileForm,
    ("preference_key", "preference_value", "value_class", "tool", "eud_uid"),
    lambda: StringField(validators=[Optional()]),
)
add_fields(DeviceProfileForm, ("enrollment", "connection", "active"), flag)

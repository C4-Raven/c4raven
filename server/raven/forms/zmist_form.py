from flask_wtf import FlaskForm
from wtforms import StringField

from raven.forms.fields import add_fields


class ZmistForm(FlaskForm):
    """The ZMIST card of a CasEvac request. Every line is free text here, z included, although
    the model stores z as an integer."""


add_fields(ZmistForm, ("i", "m", "s", "t", "title", "z"), StringField)

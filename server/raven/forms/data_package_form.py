from flask_wtf import FlaskForm
from wtforms import StringField
from wtforms.validators import DataRequired, Optional

from raven.forms.fields import flag


class DataPackageUpdateForm(FlaskForm):
    """Install flags of an existing data package, which is picked by its hash."""

    hash = StringField(validators=[DataRequired()])
    install_on_enrollment = flag(validators=[Optional()])
    install_on_connection = flag(validators=[Optional()])

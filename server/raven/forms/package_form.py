import semver
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import StringField
from wtforms.validators import DataRequired, Optional, ValidationError

from raven.forms.fields import flag


def _semver(form, field):
    """Accept an empty value, otherwise insist on a complete semantic version (5.5.1, not 5.5)."""
    version = field.data
    if version:
        try:
            semver.Version.parse(version)
        except ValueError as error:
            raise ValidationError(str(error))


def _optional_text(*checks):
    return StringField(validators=[Optional(), *checks])


class PackageForm(FlaskForm):
    """Multipart upload of a new APK (POST /api/packages)."""

    platform = StringField(default="Android")
    plugin_type = StringField(default="plugin")
    apk = FileField(validators=[FileRequired(), FileAllowed(["apk"])])
    icon = FileField(validators=[Optional()])
    description = _optional_text()
    install_on_enrollment = flag()
    install_on_connection = flag()
    atak_version = _optional_text()


class PackageUpdateForm(FlaskForm):
    """JSON update of a listed package's install flags and ATAK version (PATCH /api/packages)."""

    package_name = StringField(validators=[DataRequired()])
    install_on_enrollment = flag()
    install_on_connection = flag()
    atak_version = _optional_text(_semver)

from flask_wtf import FlaskForm
from flask_wtf.form import _Auto
from wtforms import BooleanField, IntegerField, StringField

from raven.forms.fields import add_fields

# MediaMTX path options accepted from clients, in MediaMTX's own spelling and order.
# str: text, int: whole number, True/False: a checkbox with that default.
PATH_OPTIONS = (
    ("name", str),
    ("source", str),
    ("sourceFingerprint", str),
    ("sourceOnDemand", True),
    ("sourceOnDemandStartTimeout", str),
    ("sourceOnDemandCloseAfter", str),
    ("maxReaders", int),
    ("srtReadPassphrase", str),
    ("fallback", str),
    ("record", False),
    ("recordPath", str),
    ("recordFormat", str),
    ("recordPartDuration", str),
    ("recordSegmentDuration", str),
    ("recordDeleteAfter", str),
    ("publishUser", str),
    ("publishPass", str),
    ("readUser", str),
    ("readPass", str),
    ("overridePublisher", False),
    ("srtPublishPassphrase", str),
    ("rtspTransport", str),
    ("rtspAnyPort", False),
    ("rtspRangeType", str),
    ("rtspRangeStart", str),
    ("sourceRedirect", str),
    ("rpiCameraCamID", int),
    ("rpiCameraWidth", int),
    ("rpiCameraHeight", int),
    ("rpiCameraHFlip", False),
    ("rpiCameraVFlip", False),
    ("rpiCameraBrightness", int),
    ("rpiCameraContrast", int),
    ("rpiCameraSaturation", int),
    ("rpiCameraSharpness", int),
    ("rpiCameraExposure", str),
    ("rpiCameraAWB", str),
    ("rpiCameraDenoise", str),
    ("rpiCameraShutter", int),
    ("rpiCameraMetering", str),
    ("rpiCameraGain", int),
    ("rpiCameraEV", int),
    ("rpiCameraROI", str),
    ("rpiCameraHDR", False),
    ("rpiCameraTuningFile", str),
    ("rpiCameraMode", str),
    ("rpiCameraFPS", int),
    ("rpiCameraIDRPeriod", int),
    ("rpiCameraBitrate", int),
    ("rpiCameraProfile", str),
    ("rpiCameraLevel", str),
    ("rpiCameraAfMode", str),
    ("rpiCameraAfRange", str),
    ("rpiCameraAfSpeed", str),
    ("rpiCameraLensPosition", int),
    ("rpiCameraAfWindow", str),
    ("rpiCameraTextOverlayEnable", False),
    ("rpiCameraTextOverlay", str),
    ("runOnInit", str),
    ("runOnInitRestart", False),
    ("runOnDemand", str),
    ("runOnDemandRestart", False),
    ("runOnDemandStartTimeout", str),
    ("runOnDemandCloseAfter", str),
    ("runOnUnDemand", str),
    ("runOnReady", str),
    ("runOnReadyRestart", False),
    ("runOnNotReady", str),
    ("runOnRead", str),
    ("runOnReadRestart", False),
    ("runOnUnread", str),
    ("runOnRecordSegmentCreate", str),
    ("runOnRecordSegmentComplete", str),
)


def _field_for(kind):
    if kind is str:
        return StringField
    if kind is int:
        return IntegerField
    return lambda: BooleanField(default=kind)


class MediaMTXPathConfig(FlaskForm):
    """Settings for one MediaMTX path, under MediaMTX's option names. When the form is not
    filled from a request, pass csrf_enabled=False (or meta={"csrf": False})."""

    def __init__(self, formdata=_Auto, **kwargs):
        # Leaving formdata out makes Flask-WTF read the request; formdata=None means defaults only
        csrf_wanted = kwargs.pop("csrf_enabled", True)
        if csrf_wanted is False:
            kwargs["meta"] = dict(kwargs.get("meta", {}), csrf=False)
        super().__init__(formdata, **kwargs)

    def serialize(self):
        """{option: value} for every path option (the CSRF token is not one)."""
        return {field.name: field.data for field in self if field.name != "csrf_token"}


for _name, _kind in PATH_OPTIONS:
    add_fields(MediaMTXPathConfig, (_name,), _field_for(_kind))

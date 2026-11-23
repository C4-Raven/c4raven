from wtforms import BooleanField, FieldList, FloatField, FormField, IntegerField, StringField
from wtforms.validators import UUID, DataRequired

from raven.forms.fields import add_fields
from raven.forms.point_form import PointForm
from raven.forms.zmist_form import ZmistForm

# MEDEVAC 9-line fields by input type. Field names match the CasEvac columns.
COUNTS = (
    "ambulatory", "child", "convenience", "enemy", "epw", "hlz_marking", "litter",
    "nonus_civilian", "nonus_military", "priority", "routine", "security", "urgent",
    "urgent_surgical", "us_civilian", "us_military", "zone_prot_selection",
)  # fmt: skip
# terrain_other_detail is text in the database but is read as a checkbox here
CHECKBOXES = (
    "casevac", "equipment_none", "equipment_other", "extraction_equipment", "hoist",
    "terrain_loose", "terrain_other", "terrain_other_detail", "terrain_none", "terrain_rough",
    "terrain_slope", "ventilator",
)  # fmt: skip
NUMBERS = ("freq",)
REMARKS = (
    "equipment_detail", "friendlies", "hlz_remarks", "marked_by", "medline_remarks",
    "obstacles", "terrain_detail", "terrain_slope_dir", "winds_are_from",
)  # fmt: skip


class CasEvacForm(PointForm):
    """A CASEVAC/MEDEVAC request, located by the PointForm fields it extends."""

    uid = StringField(validators=[DataRequired(), UUID()])
    title = StringField(validators=[DataRequired()])


for _names, _field_type in (
    (COUNTS, IntegerField),
    (CHECKBOXES, BooleanField),
    (NUMBERS, FloatField),
    (REMARKS, StringField),
):
    add_fields(CasEvacForm, _names, _field_type)

CasEvacForm.zmist = FieldList(FormField(ZmistForm), min_entries=0)

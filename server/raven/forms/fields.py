"""Helpers for declaring form fields from name lists."""

from wtforms import BooleanField

from raven.functions import false_values


def add_fields(form_class, names, make_field):
    """Give form_class one field per name, made by make_field() in the order the names come.
    WTForms keeps fields in creation order, which is the order a form iterates them in."""
    for name in names:
        setattr(form_class, name, make_field())


def flag(**options) -> BooleanField:
    """A checkbox that also reads "false", "0", "off", ... (raven.functions.false_values) as
    unchecked, so JSON request bodies can switch it off."""
    return BooleanField(false_values=false_values, **options)

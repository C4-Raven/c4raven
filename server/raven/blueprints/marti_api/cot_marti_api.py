"""CoT retrieval by uid (Data Sync), plus two placeholder routes."""

import datetime
from xml.etree.ElementTree import Element, fromstring, tostring

from flask import Blueprint, jsonify, request
from flask_babel import gettext
from sqlalchemy import select

from raven.extensions import db
from raven.functions import datetime_from_iso8601_string
from raven.marti_auth import marti_caller_required
from raven.models.CoT import CoT

cot_marti_api = Blueprint("cot_api", __name__)


def _naive_utc(value: datetime.datetime) -> datetime.datetime:
    """CoT times are stored as naive UTC"""
    if value.tzinfo is not None:
        value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


@cot_marti_api.route("/Marti/api/cot", methods=["GET"])
def get_cot():
    return "", 200


@cot_marti_api.route("/Marti/api/cot/sa", methods=["GET"])
def get_cot_sa():
    return "", 200


@cot_marti_api.route("/Marti/api/cot/xml/<uid>", methods=["GET"])
@marti_caller_required
def get_cot_xml(uid):
    cot = db.session.execute(select(CoT).filter_by(uid=uid)).scalars().first()
    if cot is None:
        return jsonify({"success": False, "error": gettext("No CoT found for UID %(uid)s", uid=uid)}), 404
    return cot.xml, 200


@cot_marti_api.route("/Marti/api/cot/xml/<uid>/all", methods=["GET"])
@marti_caller_required
def get_all_cot_xml(uid):
    query = select(CoT).filter(CoT.uid == uid)

    sec_ago = request.args.get("secago")
    if sec_ago is not None:
        try:
            seconds = int(sec_ago)
        except ValueError:
            return (
                jsonify({"success": False, "error": gettext("Invalid secago value: %(sec_ago)s", sec_ago=sec_ago)}),
                400,
            )
        cutoff = _naive_utc(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds))
        query = query.filter(CoT.start >= cutoff)

    start = request.args.get("start")
    if start:
        query = query.filter(CoT.start >= _naive_utc(datetime_from_iso8601_string(start)))

    end = request.args.get("end")
    if end:
        query = query.filter(CoT.stale <= _naive_utc(datetime_from_iso8601_string(end)))

    events = Element("events")
    for cot in db.session.execute(query).scalars():
        events.append(fromstring(cot.xml))

    return tostring(events).decode(), 200

"""
CASEVAC / MEDEVAC 9-line requests from the web map: list, create/change, delete.

A new or changed request becomes a b-r-f-h-c CoT with a _medevac_ detail, routed to the caller's
groups.
"""

import uuid
from datetime import timedelta
from xml.etree.ElementTree import tostring

import bleach
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from flask_security import auth_required, current_user
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import ImmutableMultiDict

from raven.blueprints.raven_api.api import paginate, route_cot, search
from raven.extensions import db, logger, socketio
from raven.forms.casevac_form import CasEvacForm
from raven.forms.zmist_form import ZmistForm
from raven.functions import generate_delete_cot
from raven.models.CasEvac import FORM_FIELDS, CasEvac
from raven.models.CoT import CoT
from raven.models.EUD import EUD
from raven.models.Point import Point
from raven.models.ZMIST import ZMIST
from raven.ownership import can_modify_device_item

casevac_api_blueprint = Blueprint("casevac_api_blueprint", __name__)

CASEVAC_TYPE = "b-r-f-h-c"


def _forbidden(message):
    return jsonify({"success": False, "error": message}), 403


def _edit_forbidden():
    return _forbidden(gettext("Only an administrator or the owner of the device that sent it can change this CASEVAC"))


@casevac_api_blueprint.route("/api/casevac")
@auth_required()
def query_casevac():
    query = db.session.query(CasEvac)
    if request.args.get("callsign"):
        # The callsign of the device that sent the request
        query = query.join(EUD, EUD.uid == CasEvac.sender_uid)
        query = search(query, EUD, "callsign")
    for field in ("sender_uid", "uid"):
        query = search(query, CasEvac, field)
    return paginate(query, CasEvac)


@casevac_api_blueprint.route("/api/casevac", methods=["POST"])
@auth_required()
def add_casevac():
    form = CasEvacForm()
    if not form.validate():
        return jsonify({"success": False, "errors": form.errors}), 400

    body = request.get_json(silent=True)
    zmist_form = None
    zmist_data = body.get("zmist") if isinstance(body, dict) else None
    if zmist_data:
        if not isinstance(zmist_data, dict):
            return jsonify({"success": False, "errors": {"zmist": [gettext("Invalid ZMIST")]}}), 400
        zmist_form = ZmistForm(formdata=ImmutableMultiDict(zmist_data))
        if not zmist_form.validate():
            return jsonify({"success": False, "errors": zmist_form.errors}), 400

    uid = form.uid.data
    existing = db.session.query(CasEvac).filter_by(uid=uid).first()
    if existing is not None and not can_modify_device_item(current_user, existing.sender_uid):
        return _edit_forbidden()

    node_id = app.config.get("RAVEN_NODE_ID")
    timestamp = form.timestamp.data
    stale = timestamp + timedelta(days=365)

    point = Point()
    point.from_wtform(form)
    point.uid = str(uuid.uuid4())
    point.device_uid = node_id
    point.timestamp = timestamp

    # Built outside the session so the CoT can be made before anything is saved
    casevac = CasEvac()
    casevac.from_wtforms(form)
    casevac.sender_uid = existing.sender_uid if existing is not None else node_id
    zmist = None
    if zmist_form is not None:
        zmist = ZMIST()
        zmist.from_wtform(zmist_form)
        zmist.casevac_uid = uid
        casevac.zmist = zmist
    casevac.point = point
    xml = tostring(casevac.to_cot()).decode()

    cot = CoT()
    cot.how = "h-g-i-g-o"
    cot.type = CASEVAC_TYPE
    cot.uid = uid
    cot.sender_uid = node_id
    cot.sender_callsign = node_id
    cot.timestamp = timestamp
    cot.start = timestamp
    cot.stale = stale
    cot.xml = xml
    db.session.add(cot)
    db.session.flush()
    point.cot_id = cot.id

    if existing is None:
        casevac.cot_id = cot.id
        try:
            with db.session.begin_nested():
                db.session.add(casevac)
            target = casevac
        except IntegrityError:
            # Created meanwhile: change that one instead
            existing = db.session.query(CasEvac).filter_by(uid=uid).one()
            if not can_modify_device_item(current_user, existing.sender_uid):
                db.session.rollback()
                return _edit_forbidden()

    if existing is not None:
        # The edit keeps the device that sent the request; its ZMIST is not changed
        casevac.point = None
        casevac.zmist = None
        db.session.add(point)
        db.session.flush()
        for field in FORM_FIELDS:
            setattr(existing, field, getattr(casevac, field))
        existing.point_id = point.id
        existing.cot_id = cot.id
        target = existing

    db.session.commit()

    route_cot(xml, current_user)
    socketio.emit("casevac", target.to_json(), namespace="/socket.io")
    return jsonify({"success": True})


@casevac_api_blueprint.route("/api/casevac", methods=["DELETE"])
@auth_required()
def delete_casevac():
    raw_uid = request.args.get("uid")
    problem = None
    if not raw_uid:
        problem = "Please specify a UID"
    else:
        uid = bleach.clean(raw_uid)
        try:
            uuid.UUID(uid)  # CASEVAC uids are always UUIDs
        except ValueError:
            problem = f"Invalid UID: {uid}"
    if problem:
        return jsonify({"success": False, "error": problem}), 400

    casevac = db.session.query(CasEvac).filter_by(uid=uid).first()
    if casevac is None:
        return jsonify({"success": False, "error": gettext("Unknown UID: %(uid)s", uid=uid)}), 404
    if not can_modify_device_item(current_user, casevac.sender_uid):
        return _forbidden(
            gettext("Only an administrator or the owner of the device that sent it can delete this CASEVAC")
        )

    event = generate_delete_cot(uid, casevac.cot.type)
    route_cot(tostring(event).decode(), current_user)

    db.session.delete(casevac)
    db.session.commit()
    logger.info(f"{current_user.username} deleted CASEVAC {uid}")
    return jsonify({"success": True})

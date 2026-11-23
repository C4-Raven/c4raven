"""Recent device telemetry (ATAK device stats) for the web UI."""

from flask import Blueprint, jsonify
from flask_security import auth_required

from raven.blueprints.raven_api.api import search
from raven.extensions import db
from raven.models.EUDStats import EUDStats

eud_stats_blueprint = Blueprint("eud_stats_blueprint", __name__)

_LATEST_ROWS = 50


@eud_stats_blueprint.route("/api/eud_stats", methods=["GET"])
@auth_required()
def get_stats():
    query = search(db.session.query(EUDStats), EUDStats, "eud_uid")
    rows = query.order_by(EUDStats.id.desc()).limit(_LATEST_ROWS).all()
    rows.reverse()
    results = [row.to_json() for row in rows]
    return jsonify({"results": results, "total_pages": 1, "current_page": 1, "per_page": len(results)})

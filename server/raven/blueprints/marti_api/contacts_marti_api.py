"""TAK contact list."""

from flask import Blueprint, jsonify
from sqlalchemy import select

from raven.extensions import db
from raven.marti_auth import marti_caller_required
from raven.models.EUD import EUD

contacts_api = Blueprint("contacts_api", __name__)


@contacts_api.route("/Marti/api/contacts/all", methods=["GET"])
@marti_caller_required
def get_all_contacts():
    contacts = []
    for eud in db.session.execute(select(EUD)).scalars():
        contacts.append(
            {
                "filterGroups": [],
                "notes": eud.user.username if eud.user else "",
                "callsign": eud.callsign,
                "team": eud.team.name if eud.team else "Cyan",
                "role": eud.team_role or "Team Member",
                "takv": f"{eud.platform} {eud.version}",
                "uid": eud.uid,
            }
        )
    return jsonify(contacts)

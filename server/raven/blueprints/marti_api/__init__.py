"""The Marti API (what ATAK, WinTAK and iTAK talk to) as one blueprint for the app to register.

The child Blueprint objects are re-exported here under their own names. One of them, marti_api,
has the same name as its module, so code that needs the module (to monkeypatch
verify_client_cert, say) imports it with importlib.import_module(
"raven.blueprints.marti_api.marti_api").
"""

from flask import Blueprint

from raven.blueprints.marti_api.certificate_enrollment_api import certificate_authority_api_blueprint
from raven.blueprints.marti_api.citrap_api import citrap_api_blueprint
from raven.blueprints.marti_api.contacts_marti_api import contacts_api
from raven.blueprints.marti_api.cot_marti_api import cot_marti_api
from raven.blueprints.marti_api.data_package_marti_api import data_package_marti_api
from raven.blueprints.marti_api.device_profile_marti_api import device_profile_marti_api_blueprint
from raven.blueprints.marti_api.group_marti_api import group_api
from raven.blueprints.marti_api.marti_api import marti_api
from raven.blueprints.marti_api.mission_marti_api import mission_marti_api
from raven.blueprints.marti_api.video_marti_api import video_marti_api

marti_blueprint = Blueprint("marti_blueprint", __name__)

# Kept in this order: the app and tests rely on the endpoint names and route set it produces
for _child in (
    data_package_marti_api,
    marti_api,
    cot_marti_api,
    device_profile_marti_api_blueprint,
    citrap_api_blueprint,
    certificate_authority_api_blueprint,
    group_api,
    mission_marti_api,
    contacts_api,
    video_marti_api,
):
    marti_blueprint.register_blueprint(_child)

__all__ = [
    "marti_blueprint",
    "certificate_authority_api_blueprint",
    "citrap_api_blueprint",
    "contacts_api",
    "cot_marti_api",
    "data_package_marti_api",
    "device_profile_marti_api_blueprint",
    "group_api",
    "marti_api",
    "mission_marti_api",
    "video_marti_api",
]

"""The UI language list and the per-session language for server-side (gettext) messages."""

from flask import Blueprint
from flask import current_app as app
from flask import jsonify, session
from flask_babel import gettext

from raven.extensions import babel

language_api = Blueprint("language_api", __name__)


@language_api.route("/api/language/test", methods=["GET"])
def test_lang():
    return jsonify({"success": True, "message": gettext("This is a test")})


@language_api.route("/api/language", methods=["GET"])
def get_languages():
    return jsonify(app.config.get("RAVEN_LANGUAGES"))


@language_api.route("/api/language/<lang_code>", methods=["PUT"])
def set_language(lang_code):
    available = {str(locale) for locale in babel.list_translations()}
    if lang_code not in available:
        return jsonify({"success": False, "error": gettext(f"{lang_code} is not a supported language")}), 400
    session["language"] = lang_code
    return jsonify({"success": True})

"""Device profiles: the ATAK settings and files pushed to devices.

Two APIs: the legacy per-preference /api/profiles (table device_profiles, no longer delivered to
devices, kept for older clients of this API) and the named profile sets at /api/device-profiles.
Validation, resolution and file storage live in raven/device_profiles.py; delivery is in the
Marti device-profile API.
"""

import os
import traceback

import bleach
import sqlalchemy.exc
from flask import Blueprint, jsonify, request, send_file
from flask_babel import gettext
from flask_security import auth_required, current_user, roles_required
from sqlalchemy import func
from werkzeug.datastructures import ImmutableMultiDict

from raven import device_profiles
from raven.admin_view import admin_view
from raven.audit import record_audit_event
from raven.blueprints.raven_api.api import paginate, search
from raven.device_profiles import ProfileValidationError
from raven.extensions import db, logger
from raven.models.DeviceProfileFile import DeviceProfileFile
from raven.models.DeviceProfiles import DeviceProfiles
from raven.models.DeviceProfileSet import DeviceProfileSet, utcnow

device_profile_api_blueprint = Blueprint("device_profile_api_blueprint", __name__)

AUDIT_CATEGORY = "device_profiles"
_MAX_DETAILS = 4000


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def _actor():
    return current_user.username if current_user and current_user.is_authenticated else None


# --------------------------------------------------------------------------------------------
# Legacy per-preference API
# --------------------------------------------------------------------------------------------


@device_profile_api_blueprint.route("/api/profiles", methods=["GET"])
@admin_view("device_profiles")
def get_device_profiles():
    query = db.session.query(DeviceProfiles)
    for field in ("preference_key", "preference_value", "tool"):
        query = search(query, DeviceProfiles, field)
    return paginate(query)


@device_profile_api_blueprint.route("/api/profiles", methods=["POST"])
@auth_required()
@roles_required("administrator")
def add_device_profile():
    from raven.forms.device_profile_form import DeviceProfileForm

    if request.is_json:
        form = DeviceProfileForm(formdata=ImmutableMultiDict(request.get_json(silent=True) or {}))
    else:
        form = DeviceProfileForm()
    if not form.validate():
        return jsonify({"success": False, "errors": form.errors}), 400

    profile = (
        db.session.query(DeviceProfiles)
        .filter_by(preference_key=form.preference_key.data, eud_uid=form.eud_uid.data)
        .first()
    )
    if profile is None:
        profile = DeviceProfiles()
        db.session.add(profile)
    profile.from_wtf(form)
    db.session.commit()
    return jsonify({"success": True})


@device_profile_api_blueprint.route("/api/profiles", methods=["DELETE"])
@roles_required("administrator")
def delete_device_profile():
    raw_key, eud_uid = request.args.get("preference_key"), request.args.get("eud_uid")
    if not raw_key:
        return _error(gettext("Please specify the preference_key"))
    preference_key = bleach.clean(raw_key)
    criteria = {"preference_key": preference_key}
    if eud_uid:
        # Without an EUD the first matching profile (global or per-device) goes
        criteria["eud_uid"] = bleach.clean(eud_uid)
    try:
        profile = db.session.query(DeviceProfiles).filter_by(**criteria).first()
        if profile is None:
            return _error(gettext("Unknown preference_key: %(preference_key)s", preference_key=preference_key), 404)
        db.session.delete(profile)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to delete device profile {preference_key}: {e}")
        return _error(str(e))


# --------------------------------------------------------------------------------------------
# Profile sets
# --------------------------------------------------------------------------------------------


def _audience(profile: DeviceProfileSet) -> str:
    parts = []
    if profile.target_everyone:
        parts.append("everyone")
    parts.extend(f"{t.kind} {t.value}" for t in profile.targets)
    return ", ".join(parts) if parts else "nobody"


def _when(profile: DeviceProfileSet) -> str:
    if profile.tool:
        return f"tool {profile.tool}"
    phases = []
    if profile.apply_on_enrollment:
        phases.append("enrollment")
    if profile.apply_on_connection:
        phases.append("connection")
    return ", ".join(phases) if phases else "never"


def _details(profile: DeviceProfileSet, extra: str | None = None) -> str:
    """Audit text for a profile. Setting values are never included: they can be passwords."""
    keys = [s.key for s in profile.settings]
    paths = [f.zip_path for f in profile.files]
    parts = [f"Profile: {profile.name}"]
    if extra:
        parts.append(extra)
    parts += [
        f"Enabled: {'yes' if profile.enabled else 'no'}",
        f"Audience: {_audience(profile)}",
        f"When: {_when(profile)}",
        f"Settings ({len(keys)}): {', '.join(keys)}" if keys else "Settings: none",
        f"Files ({len(paths)}): {', '.join(paths)}" if paths else "Files: none",
    ]
    return " · ".join(parts)[:_MAX_DETAILS]


def _audit(action, details):
    record_audit_event(AUDIT_CATEGORY, action, actor=_actor(), details=details)


def _get_profile(profile_id):
    return db.session.get(DeviceProfileSet, profile_id)


def _profile_not_found():
    return _error(gettext("Profile not found"), 404)


def _name_taken():
    return _error(gettext("A profile with that name already exists"))


@device_profile_api_blueprint.route("/api/device-profiles", methods=["GET"])
@auth_required()
@admin_view("device_profiles")
def list_profile_sets():
    profiles = db.session.query(DeviceProfileSet).order_by(func.lower(DeviceProfileSet.name)).all()
    return jsonify(device_profiles.profiles_to_dicts(profiles))


@device_profile_api_blueprint.route("/api/device-profiles", methods=["POST"])
@auth_required()
@roles_required("administrator")
def create_profile_set():
    try:
        cleaned = device_profiles.validate_profile_input(request.get_json(silent=True))
    except ProfileValidationError as e:
        return _error(str(e))
    profile = DeviceProfileSet()
    device_profiles.apply_profile_input(profile, cleaned)
    db.session.add(profile)
    try:
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
        return _name_taken()
    _audit("profile_created", _details(profile))
    return jsonify({"success": True, "profile": device_profiles.profile_to_dict(profile)})


@device_profile_api_blueprint.route("/api/device-profiles/<int:profile_id>", methods=["PUT"])
@auth_required()
@roles_required("administrator")
def update_profile_set(profile_id):
    profile = _get_profile(profile_id)
    if profile is None:
        return _profile_not_found()
    try:
        cleaned = device_profiles.validate_profile_input(request.get_json(silent=True), profile_id=profile.id)
    except ProfileValidationError as e:
        return _error(str(e))
    old_name = profile.name
    device_profiles.apply_profile_input(profile, cleaned)
    try:
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
        return _name_taken()
    extra = f"Renamed from: {old_name}" if old_name != profile.name else None
    _audit("profile_updated", _details(profile, extra))
    return jsonify({"success": True, "profile": device_profiles.profile_to_dict(profile)})


@device_profile_api_blueprint.route("/api/device-profiles/<int:profile_id>", methods=["DELETE"])
@auth_required()
@roles_required("administrator")
def delete_profile_set(profile_id):
    profile = _get_profile(profile_id)
    if profile is None:
        return _profile_not_found()
    details = _details(profile)
    stored = [f.stored_name for f in profile.files]
    db.session.delete(profile)
    db.session.commit()
    device_profiles.delete_stored_files(stored)
    _audit("profile_deleted", details)
    return jsonify({"success": True})


def _copy_name(name: str) -> str:
    def taken(candidate):
        return (
            db.session.query(DeviceProfileSet.id).filter(func.lower(DeviceProfileSet.name) == candidate.lower()).first()
            is not None
        )

    candidate = f"{name} (copy)"
    n = 2
    while taken(candidate):
        candidate = f"{name} (copy {n})"
        n += 1
    return candidate


@device_profile_api_blueprint.route("/api/device-profiles/<int:profile_id>/duplicate", methods=["POST"])
@auth_required()
@roles_required("administrator")
def duplicate_profile_set(profile_id):
    source = _get_profile(profile_id)
    if source is None:
        return _profile_not_found()
    name = _copy_name(source.name)
    if len(name) > device_profiles.MAX_NAME_LENGTH:
        return _error(gettext("The profile name is too long to copy"))

    copy = DeviceProfileSet()
    device_profiles.apply_profile_input(
        copy,
        {
            "name": name,
            "description": source.description,
            "enabled": source.enabled,
            "apply_on_enrollment": source.apply_on_enrollment,
            "apply_on_connection": source.apply_on_connection,
            "target_everyone": source.target_everyone,
            "tool": source.tool,
            "targets": [{"kind": t.kind, "value": t.value} for t in source.targets],
            "settings": [{"key": s.key, "value": s.value, "value_class": s.value_class} for s in source.settings],
        },
    )
    try:
        device_profiles.copy_profile_files(source, copy)
    except OSError as e:
        logger.error(f"Could not copy the files of profile {source.name}: {e}")
        device_profiles.delete_stored_files([f.stored_name for f in copy.files])
        return _error(gettext("Could not copy the profile's files"), 500)

    db.session.add(copy)
    try:
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
        device_profiles.delete_stored_files([f.stored_name for f in copy.files])
        return _name_taken()
    _audit("profile_duplicated", _details(copy, f"Copied from: {source.name}"))
    return jsonify({"success": True, "profile": device_profiles.profile_to_dict(copy)})


@device_profile_api_blueprint.route("/api/device-profiles/files/limits", methods=["GET"])
@auth_required()
@admin_view("device_profiles")
def get_file_limits():
    return jsonify(device_profiles.file_limits())


@device_profile_api_blueprint.route("/api/device-profiles/<int:profile_id>/files", methods=["POST"])
@auth_required()
@roles_required("administrator")
def upload_profile_files(profile_id):
    profile = _get_profile(profile_id)
    if profile is None:
        return _profile_not_found()
    uploads = [f for f in request.files.getlist("file") if f and f.filename]
    if not uploads:
        return _error(gettext("Choose a file to upload"))
    folder = request.form.get("folder")

    added, replaced = [], []
    try:
        for upload in uploads:
            row, old = device_profiles.add_profile_file(
                profile, upload.stream, upload.filename, folder, upload.mimetype, _actor()
            )
            added.append(row)
            if old is not None:
                replaced.append(old)
        db.session.commit()
    except ProfileValidationError as e:
        db.session.rollback()
        device_profiles.delete_stored_files([r.stored_name for r in added])
        return _error(str(e))
    except (sqlalchemy.exc.IntegrityError, OSError) as e:
        db.session.rollback()
        logger.error(f"Could not store a file of profile {profile_id}: {e}")
        logger.debug(traceback.format_exc())
        device_profiles.delete_stored_files([r.stored_name for r in added])
        return _error(gettext("Could not store that file"), 500)

    # A file replaced by a later one in this same request isn't reported (or kept)
    kept = [r for r in added if not any(r is old for old in replaced)]
    device_profiles.delete_stored_files([r.stored_name for r in replaced])
    _audit(
        "profile_file_added",
        _details(
            profile,
            "Uploaded: " + ", ".join(f"{r.zip_path} ({r.size} bytes, sha256 {r.sha256[:12]})" for r in kept),
        ),
    )
    return jsonify(
        {
            "success": True,
            "files": [device_profiles.file_to_dict(r) for r in kept],
            "profile": device_profiles.profile_to_dict(profile),
        }
    )


def _get_file(profile_id, file_id):
    row = db.session.get(DeviceProfileFile, file_id)
    if row is None or row.profile_id != profile_id:
        return None
    return row


@device_profile_api_blueprint.route("/api/device-profiles/<int:profile_id>/files/<int:file_id>", methods=["GET"])
@auth_required()
@roles_required("administrator")
def download_profile_file(profile_id, file_id):
    row = _get_file(profile_id, file_id)
    if row is None:
        return _error(gettext("File not found"), 404)
    path = device_profiles.stored_file_path(row)
    if not os.path.isfile(path):
        return _error(gettext("File not found"), 404)
    return send_file(path, as_attachment=True, download_name=row.filename, mimetype="application/octet-stream")


@device_profile_api_blueprint.route("/api/device-profiles/<int:profile_id>/files/<int:file_id>", methods=["DELETE"])
@auth_required()
@roles_required("administrator")
def delete_profile_file(profile_id, file_id):
    row = _get_file(profile_id, file_id)
    if row is None:
        return _error(gettext("File not found"), 404)
    profile = row.profile
    stored_name, zip_path = row.stored_name, row.zip_path
    profile.files.remove(row)
    db.session.delete(row)
    profile.updated_at = utcnow()
    db.session.commit()
    device_profiles.delete_stored_files([stored_name])
    _audit("profile_file_removed", _details(profile, f"Removed: {zip_path}"))
    return jsonify({"success": True, "profile": device_profiles.profile_to_dict(profile)})

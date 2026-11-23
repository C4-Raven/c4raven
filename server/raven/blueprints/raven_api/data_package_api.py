"""Data packages in the web UI: list, upload, download and delete them, and (administrators) set
their install-on-enrollment/connection flags and the groups they're limited to.

Visibility follows the Marti data package rules (raven/group_isolation.py and
data_package_marti_api): private packages only for their sender and recipients, group-limited
packages only for members of one of their groups."""

import os
import shutil
import traceback
from datetime import datetime, timezone

import bleach
from flask import Blueprint, current_app as app, jsonify, request, send_from_directory
from flask_babel import gettext
from flask_security import auth_required, current_user
from werkzeug.datastructures import ImmutableMultiDict

from raven.audit import record_audit_event
from raven.blueprints.raven_api.api import paginate, search
from raven.extensions import db, logger
from raven.group_isolation import resolve_group_names
from raven.models.Certificate import Certificate
from raven.models.DataPackage import DataPackage

data_package_api = Blueprint("data_package_api", __name__)


def _marti():
    import importlib

    return importlib.import_module("raven.blueprints.marti_api.data_package_marti_api")


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


def _is_admin() -> bool:
    return current_user.has_role("administrator")


def _admin_required():
    return _error(gettext("Administrator role required"), 403)


def _find(file_hash):
    return db.session.query(DataPackage).filter_by(hash=file_hash).first()


@data_package_api.route("/api/data_packages", methods=["GET"])
@auth_required()
def get_data_packages():
    query = db.session.query(DataPackage)
    for field in ("filename", "hash", "creator_uid", "keywords", "mime_type", "size", "tool"):
        query = search(query, DataPackage, field)
    query = _marti().visible_data_packages(query, current_user, admin_sees_all=True)
    return paginate(query, DataPackage)


@data_package_api.route("/api/data_packages", methods=["POST"])
@auth_required()
def upload_data_package():
    return _marti().data_package_share()


@data_package_api.route("/api/data_packages", methods=["PATCH"])
@auth_required()
def edit_data_package():
    from raven.forms.data_package_form import DataPackageUpdateForm

    if not _is_admin():
        return _admin_required()
    form = DataPackageUpdateForm(formdata=ImmutableMultiDict(request.get_json(silent=True) or {}))
    if not form.validate():
        return jsonify({"success": False, "errors": form.errors}), 400

    data_package = _find(form.hash.data)
    if data_package is None:
        return _error(f"Package with hash {bleach.clean(str(form.hash.data))} not found", 404)
    if (data_package.filename or "").endswith("_CONFIG.zip"):
        return _error(gettext("Server connection data packages can't be installed on enrollment or connection"), 400)
    enrollment = form.install_on_enrollment.data
    connection = form.install_on_connection.data
    if data_package.is_private and (enrollment or connection):
        return _error(gettext("Private data packages can't be installed on enrollment or connection"), 400)

    # Only the flags present in the request change
    for column, value in (("install_on_enrollment", enrollment), ("install_on_connection", connection)):
        if value is not None:
            setattr(data_package, column, value)
    data_package.submission_time = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"success": True})


@data_package_api.route("/api/data_packages", methods=["DELETE"])
@auth_required()
def delete_data_package():
    """Owner, owning-EUD user or admin only; removes the row and its <hash>.zip."""
    file_hash = request.args.get("hash")
    data_package = _find(file_hash) if file_hash else None
    if data_package is None:
        missing = "Please provide a file hash" if not file_hash else gettext("Invalid/unknown hash")
        return _error(missing, 400)

    owns_eud = bool(data_package.eud and data_package.eud.user_id == current_user.id)
    if not (data_package.submission_user == current_user.id or owns_eud or _is_admin()):
        return _error(gettext("You can only delete your own data packages"), 403)

    try:
        certificate = data_package.certificate
        common_name = certificate.common_name if certificate else None
        certificate_id = certificate.id if certificate else None
        stored_hash = data_package.hash

        db.session.delete(data_package)
        db.session.commit()

        # Only ever the <hash>.zip inside the uploads folder
        path = os.path.join(app.config.get("UPLOAD_FOLDER"), f"{os.path.basename(stored_hash)}.zip")
        if os.path.exists(path):
            os.remove(path)
        else:
            logger.warning(f"Data package file {path} was already gone")

        if certificate_id is not None:
            row = db.session.get(Certificate, certificate_id)
            if row is not None:
                db.session.delete(row)
                db.session.commit()
            if common_name:
                certs_folder = os.path.join(app.config.get("RAVEN_CA_FOLDER"), "certs")
                folder = os.path.join(certs_folder, common_name)
                # Never outside the CA's certs folder
                if os.path.dirname(os.path.abspath(folder)) == os.path.abspath(certs_folder):
                    shutil.rmtree(folder, ignore_errors=True)
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to delete data package {file_hash}: {e}")
        logger.debug(traceback.format_exc())
        return _error(str(e), 500)


@data_package_api.route("/api/data_packages/download", methods=["GET"])
@auth_required()
def download_data_package():
    file_hash = request.args.get("hash")
    if not file_hash:
        return _error(gettext("Please provide a data package hash"), 400)
    file_hash = bleach.clean(file_hash)
    data_package = _find(file_hash)
    if data_package is None:
        return _error(gettext("Data package with hash '%(hash)s' not found", hash=file_hash), 404)

    if not _marti().can_access_data_package(data_package, current_user):
        if data_package.is_private:
            return _error(gettext("This data package is private to its sender and recipients"), 403)
        return _error(gettext("This data package is limited to groups you aren't in"), 403)

    certificate = data_package.certificate
    if certificate is not None:
        record_audit_event(
            "enrollment",
            "enroll_zip",
            username=certificate.username,
            actor=current_user.username,
            details=f"Certificate package {data_package.filename} downloaded from the web UI",
        )

    _, extension = os.path.splitext(data_package.filename or "")
    return send_from_directory(
        app.config.get("UPLOAD_FOLDER"),
        f"{data_package.hash}{extension}",
        as_attachment=True,
        download_name=data_package.filename,
    )


@data_package_api.route("/api/data_packages/groups", methods=["PATCH"])
@auth_required()
def edit_data_package_groups():
    if not _is_admin():
        return _admin_required()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    file_hash = data.get("hash")
    names = data.get("groups")
    if not file_hash or not isinstance(names, list):
        return _error(gettext("Please provide a hash and a list of groups"), 400)
    data_package = _find(file_hash)
    if data_package is None:
        return _error(gettext("Data package with hash '%(hash)s' not found", hash=bleach.clean(str(file_hash))), 404)

    groups, missing = resolve_group_names(names)
    if missing:
        return _error(gettext("Group not found: %(group_name)s", group_name=", ".join(missing)), 404)

    before = data_package.group_names
    data_package.groups = groups
    db.session.commit()
    after = data_package.group_names

    if before != after:
        record_audit_event(
            "groups",
            "data_package_groups_changed",
            actor=current_user.username,
            details=(
                f"Data package: {data_package.filename} ({data_package.hash}) · Groups: "
                f"{', '.join(before) or 'everyone'} -> {', '.join(after) or 'everyone'}"
            ),
        )
    return jsonify({"success": True, "groups": after})

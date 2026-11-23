"""ATAK plugin/app update packages: the APK repository ATAK's "update server" reads
(product.infz / product.inf, repositories.inf) and the admin API to upload, edit and delete them.

Files live in <RAVEN_DATA_FOLDER>/packages/; a version-specific index lives in
<RAVEN_DATA_FOLDER>/packages/<atak_version>/ (APKs themselves are always in the top folder).
"""

import csv
import hashlib
import os
import traceback
import zipfile
from datetime import datetime, timezone

import bleach
import sqlalchemy.exc
from flask import Blueprint, current_app as app, jsonify, request, send_from_directory
from flask_security import auth_required, roles_required
from werkzeug.datastructures import ImmutableMultiDict
from werkzeug.utils import secure_filename

from raven.admin_view import admin_view
from raven.blueprints.raven_api.api import paginate, search
from raven.extensions import db, logger

packages_blueprint = Blueprint("packages_api_blueprint", __name__)

PRODUCT_INF_COLUMNS = (
    "platform",
    "plugin_type",
    "package_name",
    "name",
    "version",
    "revision_code",
    "file_name",
    "icon_filename",
    "description",
    "apk_hash",
    "os_requirement",
    "tak_prereq",
    "file_size",
)


def _packages_folder() -> str:
    return os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "packages")


def _packages_model():
    from raven.models.Packages import Packages

    return Packages


def _client_cert_ok() -> bool:
    import importlib

    marti_api = importlib.import_module("raven.blueprints.marti_api.marti_api")
    try:
        return bool(marti_api.verify_client_cert())
    except Exception as e:
        logger.debug(f"Client certificate check failed: {e}")
        return False


def _unauthorized():
    return "", 401


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_folder(atak_version) -> str:
    """The folder of an ATAK version's index, kept inside the packages folder."""
    version = secure_filename(bleach.clean(str(atak_version)))
    if not version:
        raise ValueError("Invalid ATAK version")
    return os.path.join(_packages_folder(), version)


def create_product_infz(atak_version: str | None) -> None:
    """Writes product.infz (product.inf plus the icons) for every package, or for the packages of
    one ATAK version into that version's folder."""
    Packages = _packages_model()
    folder = _version_folder(atak_version) if atak_version else _packages_folder()
    os.makedirs(folder, exist_ok=True)

    infz_path = os.path.join(folder, "product.infz")
    inf_path = os.path.join(folder, "product.inf")
    if os.path.exists(infz_path):
        os.remove(infz_path)

    query = db.session.query(Packages)
    if atak_version:
        query = query.filter_by(atak_version=bleach.clean(str(atak_version)))
    packages = query.all()

    with open(inf_path, "w", newline="") as f:
        writer = csv.writer(f)
        for package in packages:
            writer.writerow(["" if getattr(package, c) is None else getattr(package, c) for c in PRODUCT_INF_COLUMNS])

    with zipfile.ZipFile(infz_path, "w", zipfile.ZIP_DEFLATED) as infz:
        for package in packages:
            if package.icon and package.icon_filename:
                infz.writestr(secure_filename(package.icon_filename) or package.icon_filename, package.icon)
        infz.write(inf_path, "product.inf")
    os.remove(inf_path)


# --------------------------------------------------------------------------------------------
# Device routes (client certificate)
# --------------------------------------------------------------------------------------------


@packages_blueprint.route("/api/packages/repositories.inf", methods=["GET"])
def get_repositories_inf():
    if not _client_cert_ok():
        return _unauthorized()
    Packages = _packages_model()
    if db.session.query(Packages).first() is None:
        return "", 404
    versions = [
        v for (v,) in db.session.query(Packages.atak_version).distinct().order_by(Packages.atak_version).all() if v
    ]
    return "".join(f"{v}\n" for v in versions), 200


@packages_blueprint.route("/api/packages/product.infz", methods=["HEAD"])
def head_product_infz():
    if not _client_cert_ok():
        return _unauthorized()
    exists = os.path.exists(os.path.join(_packages_folder(), "product.infz"))
    return jsonify({"success": exists}), 200 if exists else 404


@packages_blueprint.route("/api/packages/<atak_version>/product.infz", methods=["HEAD"])
def head_version_product_infz(atak_version):
    if not _client_cert_ok():
        return _unauthorized()
    try:
        exists = os.path.exists(os.path.join(_version_folder(atak_version), "product.infz"))
    except ValueError:
        exists = False
    return jsonify({"success": exists}), 200 if exists else 404


@packages_blueprint.route("/api/packages/product.infz", methods=["GET"])
def get_product_infz():
    if not _client_cert_ok():
        return _unauthorized()
    return send_from_directory(_packages_folder(), "product.infz")


@packages_blueprint.route("/api/packages/<atak_version>/product.infz", methods=["GET"])
def get_version_product_infz(atak_version):
    if not _client_cert_ok():
        return _unauthorized()
    try:
        folder = _version_folder(atak_version)
    except ValueError:
        return "", 404
    return send_from_directory(folder, "product.infz")


@packages_blueprint.route("/api/packages/<package_name>", methods=["GET"])
def get_package_file(package_name):
    if not _client_cert_ok():
        return _unauthorized()
    return send_from_directory(_packages_folder(), secure_filename(package_name))


@packages_blueprint.route("/api/packages/<atak_version>/<package_name>", methods=["GET"])
def get_version_package_file(atak_version, package_name):
    if not _client_cert_ok():
        return _unauthorized()
    # APKs are always served from the top-level folder
    return send_from_directory(_packages_folder(), secure_filename(package_name))


# --------------------------------------------------------------------------------------------
# Admin API
# --------------------------------------------------------------------------------------------


@packages_blueprint.route("/api/packages", methods=["GET"])
@auth_required()
@admin_view("plugin_updates")
def get_packages():
    Packages = _packages_model()
    query = db.session.query(Packages)
    for field in (
        "platform",
        "plugin_type",
        "package_name",
        "name",
        "file_name",
        "version",
        "revision_code",
        "apk_hash",
        "tak_prereq",
        "file_size",
        "atak_version",
    ):
        query = search(query, Packages, field)
    return paginate(query)


def _remove_apk(file_name):
    if not file_name:
        return
    path = os.path.join(_packages_folder(), secure_filename(file_name))
    if os.path.exists(path):
        os.remove(path)


@packages_blueprint.route("/api/packages", methods=["POST"])
@auth_required()
@roles_required("administrator")
def add_package():
    from raven.forms.package_form import PackageForm

    Packages = _packages_model()
    form = PackageForm()
    if not form.validate():
        return jsonify({"success": False, "errors": form.errors}), 400

    folder = _packages_folder()
    os.makedirs(folder, exist_ok=True)
    file_name = secure_filename(form.apk.data.filename or "")
    if not file_name:
        return jsonify({"success": False, "errors": {"apk": ["Invalid file name"]}}), 400
    apk_path = os.path.join(folder, file_name)
    form.apk.data.save(apk_path)

    try:
        package = Packages()
        package.from_wtform(form)
    except Exception as e:
        logger.error(f"Failed to read the uploaded APK {file_name}: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"success": False, "error": f"Failed to read the APK: {e}"}), 400
    # The hash ATAK checks the download against: always the bytes on disk
    package.apk_hash = _sha256_file(apk_path)
    package.file_size = os.path.getsize(apk_path)
    package.file_name = file_name

    atak_version = form.atak_version.data or None
    package.atak_version = atak_version

    existing = db.session.query(Packages).filter_by(package_name=package.package_name, atak_version=atak_version).all()
    for old in existing:
        if old.file_name and old.file_name != file_name:
            _remove_apk(old.file_name)
        db.session.delete(old)

    try:
        db.session.add(package)
        db.session.commit()
    except sqlalchemy.exc.IntegrityError:
        db.session.rollback()
        values = {k: v for k, v in package.serialize().items()}
        db.session.query(Packages).filter_by(package_name=values["package_name"]).update(values)
        db.session.commit()

    create_product_infz(atak_version)
    return jsonify({"success": True})


@packages_blueprint.route("/api/packages", methods=["PATCH"])
@auth_required()
@roles_required("administrator")
def edit_package():
    from raven.forms.package_form import PackageUpdateForm

    Packages = _packages_model()
    form = PackageUpdateForm(formdata=ImmutableMultiDict(request.get_json(silent=True) or {}))
    if not form.validate():
        return jsonify({"success": False, "errors": form.errors}), 400

    package_name = form.package_name.data
    atak_version = form.atak_version.data or None
    package = db.session.query(Packages).filter_by(package_name=package_name, atak_version=atak_version).first()
    if package is None:
        return jsonify({"success": False, "error": f"{package_name} not found"}), 404

    package.install_on_enrollment = form.install_on_enrollment.data
    package.install_on_connection = form.install_on_connection.data
    package.publish_time = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"success": True})


@packages_blueprint.route("/api/packages", methods=["DELETE"])
@auth_required()
@roles_required("administrator")
def delete_package():
    Packages = _packages_model()
    package_name = request.args.get("package_name")
    if not package_name:
        return jsonify({"success": False, "error": "Please provide the package name of the plugin to delete"}), 400
    package_name = bleach.clean(package_name)
    atak_version = request.args.get("atak_version")
    atak_version = bleach.clean(atak_version) if atak_version else None

    query = db.session.query(Packages).filter_by(package_name=package_name)
    if atak_version:
        query = query.filter_by(atak_version=atak_version)
    package = query.first()
    if package is None:
        return jsonify({"success": False, "error": f"Unknown package name: {package_name}"}), 404

    _remove_apk(package.file_name)
    db.session.delete(package)
    db.session.commit()
    create_product_infz(atak_version)
    return jsonify({"success": True})

import base64
import hashlib
import os
from datetime import datetime, timezone

from flask import current_app, request
from sqlalchemy import LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.utils import secure_filename

from raven.extensions import db
from raven.models.columns import text
from raven.models.serialization import columns, iso

_ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


# Columns reported as stored; to_json() replaces the two that need converting
_LISTING = (
    "platform", "plugin_type", "package_name", "name", "file_name", "version", "revision_code",
    "description", "apk_hash", "os_requirement", "tak_prereq", "file_size", "icon",
    "icon_filename", "install_on_enrollment", "install_on_connection", "publish_time",
    "atak_version",
)  # fmt: skip


class Packages(db.Model):
    """An APK (ATAK plugin or app) listed in the server's plugin repository and product.inf."""

    __tablename__ = "packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str | None] = text()
    plugin_type: Mapped[str | None] = text()
    package_name: Mapped[str | None] = text()
    name: Mapped[str | None] = text()
    file_name: Mapped[str | None] = text()
    version: Mapped[str | None] = text()
    revision_code: Mapped[int | None] = mapped_column()
    description: Mapped[str | None] = text()
    apk_hash: Mapped[str | None] = text()
    os_requirement: Mapped[int | None] = mapped_column()
    # The APK's plugin-api meta-data, e.g. com.atakmap.app@5.5.1.CIV; product.inf lists it verbatim
    tak_prereq: Mapped[str | None] = text()
    file_size: Mapped[int] = mapped_column()
    icon: Mapped[bytes | None] = mapped_column(LargeBinary)
    icon_filename: Mapped[str | None] = text()
    install_on_enrollment: Mapped[bool | None] = mapped_column(default=False)
    install_on_connection: Mapped[bool | None] = mapped_column(default=False)
    publish_time: Mapped[datetime] = mapped_column()
    atak_version: Mapped[str | None] = text()

    def from_wtform(self, form):
        """Fill the row from a PackageForm whose APK has already been saved to
        {RAVEN_DATA_FOLDER}/packages/{file_name}."""
        from androguard.core.apk import APK

        self.platform = form.platform.data
        self.plugin_type = form.plugin_type.data
        self.file_name = secure_filename(form.apk.data.filename)

        apk_path = os.path.join(
            current_app.config.get("RAVEN_DATA_FOLDER"), "packages", self.file_name
        )
        apk = APK(apk_path)
        self.package_name = apk.get_package()
        self.version = apk.get_androidversion_name()
        self.revision_code = apk.get_androidversion_code()
        self.os_requirement = apk.get_min_sdk_version()
        self.name = apk.get_app_name()
        self.description = form.description.data

        # Hash the saved file: the upload stream has already been read to the end when it was saved
        sha256 = hashlib.sha256()
        with open(apk_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)
        self.apk_hash = sha256.hexdigest()
        self.file_size = os.path.getsize(apk_path)

        icon_upload = request.files.get("icon") if request else None
        if icon_upload:
            self.icon = icon_upload.read()
            self.icon_filename = secure_filename(icon_upload.filename)
        else:
            app_icon = apk.get_app_icon()
            if app_icon and app_icon.endswith(".png"):
                self.icon = apk.get_file(app_icon)
                self.icon_filename = f"{self.package_name}.png"

        self.install_on_enrollment = form.install_on_enrollment.data
        self.install_on_connection = form.install_on_connection.data
        self.atak_version = form.atak_version.data
        self.publish_time = datetime.now(timezone.utc)

        manifest = apk.get_android_manifest_xml()
        if manifest is not None:
            for meta_data in manifest.iter("meta-data"):
                if meta_data.get(f"{_ANDROID_NS}name") == "plugin-api":
                    self.tak_prereq = meta_data.get(f"{_ANDROID_NS}value")
                    break

    def serialize(self):
        return columns(self, _LISTING)

    def to_json(self):
        listing = self.serialize()
        listing["publish_time"] = iso(self.publish_time)
        icon = self.icon
        listing["icon"] = (
            "data:image/png;base64," + base64.b64encode(icon).decode("ascii") if icon else None
        )
        return listing

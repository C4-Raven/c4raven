"""Linking the server to a tak.gov account (OAuth 2.0 device authorization grant) and importing
ATAK plugins from tak.gov's catalogue into the package repository (package_api).

tak.gov refresh tokens are single use and rotate on every refresh: two refreshes racing each other
leave the server with a dead refresh token and the link broken. get_new_access_token() therefore
serialises callers with one process-wide lock and reuses the access token until it expires.
"""

import hashlib
import os
import posixpath
import re
import threading
import time
import traceback
import urllib.parse
from datetime import datetime, timezone

import bleach
import httpx
from flask import Blueprint, Response, current_app as app, jsonify, request
from flask_security import roles_required
from werkzeug.utils import secure_filename

import raven
from raven.admin_view import admin_view
from raven.blueprints.raven_api.api import change_config_setting
from raven.blueprints.raven_api.package_api import create_product_infz
from raven.extensions import db, logger

tak_gov_link_blueprint = Blueprint("tak_gov_link_blueprint", __name__)

HEADERS = {"User-Agent": f"Raven {raven.__version__}"}

CLIENT_ID = "tak-gov-eud"
REALM_URL = "https://auth.tak.gov/auth/realms/TPC/protocol/openid-connect"
DEVICE_AUTH_URL = f"{REALM_URL}/auth/device"
TOKEN_URL = f"{REALM_URL}/token"
PLUGINS_URL = "https://tak.gov/eud_api/software/v1/plugins"
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SCOPE = "openid offline_access email profile"

_TIMEOUT = httpx.Timeout(30.0, read=120.0)

_token_lock = threading.Lock()
_token_cache = {"access_token": None, "expires_at": 0.0}

_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")
_CD_FILENAME_RE = re.compile(r"""filename\*?=(?:UTF-8'')?["']?([^"';]+)["']?""", re.IGNORECASE)


def _client() -> httpx.Client:
    return httpx.Client(http2=True, headers=HEADERS, timeout=_TIMEOUT)


def _error(message, status):
    return jsonify({"success": False, "error": message}), status


def _save_setting(key, value):
    """Persists a setting to config.yml and the running app's config."""
    change_config_setting(key, value)
    app.config[key] = value


def _clear_token_cache():
    _token_cache["access_token"] = None
    _token_cache["expires_at"] = 0.0
    _products_cache.clear()


def _is_eud_api_url(url) -> bool:
    """True only for https://tak.gov/eud_api[/...] (port 443 or none, no userinfo), judged on the
    percent-decoded, normalised path, so "..", encoded dots and doubled slashes can't climb out."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.hostname != "tak.gov":
        return False
    if port not in (None, 443):
        return False
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return False
    path = urllib.parse.unquote(parts.path or "")
    if "\\" in path or "\x00" in path:
        return False
    path = re.sub(r"/+", "/", path)
    normalised = posixpath.normpath(path) if path else ""
    if normalised.startswith("//"):
        normalised = "/" + normalised.lstrip("/")
    return normalised == "/eud_api" or normalised.startswith("/eud_api/")


def get_new_access_token() -> dict:
    """A tak.gov access token, from the cache or by spending the stored refresh token. Never raises.

    Returns {"success": True, "access_token", "expires_in"} or {"success": False, "error"}."""
    with _token_lock:
        now = time.monotonic()
        if _token_cache["access_token"] and now < _token_cache["expires_at"]:
            return {
                "success": True,
                "access_token": _token_cache["access_token"],
                "expires_in": _token_cache["expires_at"] - now,
            }
        try:
            with _client() as client:
                response = client.post(
                    TOKEN_URL,
                    data={
                        "client_id": CLIENT_ID,
                        "grant_type": "refresh_token",
                        "refresh_token": app.config.get("RAVEN_TAK_GOV_REFRESH_TOKEN"),
                    },
                )
            if response.status_code != 200:
                return {"success": False, "error": f"Failed to get new access token: {response.text}"}
            body = response.json()
            access_token = body["access_token"]
            refresh_token = body["refresh_token"]
            expires_in = body["expires_in"]
            # The old refresh token is spent: store the new one before anything else can fail
            _save_setting("RAVEN_TAK_GOV_REFRESH_TOKEN", refresh_token)
            _token_cache["access_token"] = access_token
            _token_cache["expires_at"] = time.monotonic() + max(float(expires_in) - 10, 0)
            return {"success": True, "access_token": access_token, "expires_in": expires_in}
        except Exception as e:
            logger.error(f"Failed to refresh the tak.gov token: {e}")
            logger.debug(traceback.format_exc())
            return {"success": False, "error": f"Failed to get new access token: {e}"}


def _bearer(token: dict) -> dict:
    return {"Authorization": f"Bearer {token['access_token']}"}


def _content_disposition_filename(response) -> str | None:
    header = response.headers.get("Content-Disposition") or ""
    match = _CD_FILENAME_RE.search(header)
    if not match:
        return None
    name = urllib.parse.unquote(match.group(1).strip())
    # Only the last path component, then werkzeug's ASCII-safe form
    name = secure_filename(name.replace("\\", "/").rsplit("/", 1)[-1])
    return name or None


@tak_gov_link_blueprint.route("/api/takgov/link", methods=["GET"])
@roles_required("administrator")
def link_tak_gov_account():
    try:
        with _client() as client:
            response = client.post(
                DEVICE_AUTH_URL,
                data={"client_id": CLIENT_ID, "scope": SCOPE, "grant_type": DEVICE_CODE_GRANT},
            )
        if response.status_code != 200:
            return _error(f"Failed to link tak.gov account: {response.text}", response.status_code)
        body = response.json()
        device_code = body["device_code"]
        # Present in a valid answer; only the codes go back to the UI
        for key in ("verification_uri", "verification_uri_complete", "expires_in", "interval"):
            body[key]
        return jsonify({"success": True, "device_code": device_code, "user_code": body["user_code"]})
    except Exception as e:
        logger.error(f"Failed to start linking tak.gov: {e}")
        logger.debug(traceback.format_exc())
        return _error(f"Failed to link tak.gov account: {e}", 500)


@tak_gov_link_blueprint.route("/api/takgov/token", methods=["GET"])
@roles_required("administrator")
def get_initial_token():
    device_code = request.args.get("device_code")
    if not device_code:
        return _error("device_code is required", 400)
    try:
        with _client() as client:
            response = client.post(
                TOKEN_URL,
                data={"grant_type": DEVICE_CODE_GRANT, "device_code": device_code, "client_id": CLIENT_ID},
            )
        body = response.json()
        access_token = body["access_token"]
        refresh_token = body["refresh_token"]
        expires_in = body["expires_in"]
        with _token_lock:
            _save_setting("RAVEN_TAK_GOV_LINKED", True)
            _save_setting("RAVEN_TAK_GOV_REFRESH_TOKEN", refresh_token)
            _products_cache.clear()  # a different account may have different products
            _token_cache["access_token"] = access_token
            _token_cache["expires_at"] = time.monotonic() + max(float(expires_in) - 10, 0)
        return jsonify({"success": True, "access_token": access_token, "expires_in": expires_in})
    except Exception as e:
        # Includes "authorization_pending": the UI keeps polling
        return jsonify({"success": False, "error": f"Failed to get initial tak gov token: {e}"})


@tak_gov_link_blueprint.route("/api/takgov/token", methods=["PATCH"])
@roles_required("administrator")
def get_new_tak_gov_token():
    try:
        if not app.config.get("RAVEN_TAK_GOV_LINKED") or not app.config.get("RAVEN_TAK_GOV_REFRESH_TOKEN"):
            return _error("Not linked to a tak.gov account", 400)
        token = get_new_access_token()
        return jsonify(token), (200 if token.get("success") else 500)
    except Exception as e:
        return _error(f"Failed to get new tak.gov token {e}", 500)


@tak_gov_link_blueprint.route("/api/takgov/plugins", methods=["GET"])
@roles_required("administrator")
def get_plugins():
    product = request.args.get("product")
    product_version = request.args.get("product_version")
    if not product or not product_version:
        return _error("product and product_version are required", 400)
    token = get_new_access_token()
    if not token.get("success"):
        return jsonify(token), 500
    try:
        with _client() as client:
            response = client.get(
                PLUGINS_URL,
                params={"product": product, "product_version": product_version},
                headers=_bearer(token),
            )
        return jsonify(response.json()), 200
    except Exception as e:
        logger.error(f"Failed to get the tak.gov plugin list: {e}")
        return _error(f"Failed to get plugin list: {e}", 500)


# TAK.gov's products. The CIV ones are open to every account; the others are FMS-case
# products. TAK.gov answers a request for a product the account can't use with the CIV
# plugins instead of an error, so an FMS product only counts when it lists something its
# CIV product doesn't.
PRODUCT_FAMILIES = {
    "ATAK": ("ATAK-CIV", "ATAK-AUS", "ATAK-MNWG"),
    "WinTAK": ("WinTAK-CIV", "WinTAK-AUS", "WinTAK-MNWG"),
    "TAKX": ("TAKX-CIV", "TAKX-AUS", "TAKX-MNWG"),
}
_PRODUCTS_TTL = 600
_products_cache: dict = {}
_products_lock = threading.Lock()


def _plugin_key(plugin) -> tuple:
    return (plugin.get("package_name"), plugin.get("version"), plugin.get("revision_code"))


def _fetch_plugin_list(token, product, product_version):
    """The plugin list TAK.gov gives this account for one product, or None if it gave no list."""
    try:
        with _client() as client:
            response = client.get(
                PLUGINS_URL,
                params={"product": product, "product_version": product_version},
                headers=_bearer(token),
            )
        body = response.json() if response.status_code == 200 else None
    except Exception as e:
        logger.warning(f"tak.gov plugin list for {product} failed: {e}")
        return None
    return body if isinstance(body, list) else None


def available_products(token, product_version) -> list[str]:
    """The products this TAK.gov account can actually use for ``product_version``: every CIV
    product, plus each FMS product whose list has a plugin its CIV product doesn't."""
    from concurrent.futures import ThreadPoolExecutor

    all_products = [p for family in PRODUCT_FAMILIES.values() for p in family]
    with ThreadPoolExecutor(max_workers=len(all_products)) as pool:
        lists = dict(zip(all_products, pool.map(lambda p: _fetch_plugin_list(token, p, product_version), all_products)))
    products = []
    for civ, *fms in PRODUCT_FAMILIES.values():
        products.append(civ)
        civ_keys = {_plugin_key(x) for x in lists.get(civ) or []}
        for product in fms:
            plugins = lists.get(product) or []
            if any(_plugin_key(x) not in civ_keys for x in plugins):
                products.append(product)
    return products


@tak_gov_link_blueprint.route("/api/takgov/products", methods=["GET"])
@roles_required("administrator")
def get_products():
    product_version = request.args.get("product_version")
    if not product_version:
        return _error("product_version is required", 400)
    now = time.monotonic()
    with _products_lock:
        cached = _products_cache.get(product_version)
        if cached and now < cached[0]:
            return jsonify({"success": True, "products": cached[1]})
    token = get_new_access_token()
    if not token.get("success"):
        return jsonify(token), 500
    products = available_products(token, product_version)
    with _products_lock:
        _products_cache[product_version] = (now + _PRODUCTS_TTL, products)
    return jsonify({"success": True, "products": products})


@tak_gov_link_blueprint.route("/api/takgov", methods=["DELETE"])
@roles_required("administrator")
def unlink_tak_gov_account():
    if not app.config.get("RAVEN_TAK_GOV_LINKED"):
        return _error("Not linked to a TAK.gov account", 400)
    with _token_lock:
        _save_setting("RAVEN_TAK_GOV_LINKED", False)
        _save_setting("RAVEN_TAK_GOV_REFRESH_TOKEN", None)
        # A cached access token would otherwise keep working for the old account after unlinking
        _clear_token_cache()
    return jsonify({"success": True})


@tak_gov_link_blueprint.route("/api/takgov", methods=["GET"])
@admin_view("takgov")
def get_link_status():
    return jsonify({"success": True, "tak_gov_account_linked": app.config.get("RAVEN_TAK_GOV_LINKED")})


@tak_gov_link_blueprint.route("/api/takgov/icon", methods=["GET"])
@roles_required("administrator")
def get_icon():
    icon_url = request.args.get("icon_url")
    if not icon_url:
        return _error("icon_link is required", 400)
    if not _is_eud_api_url(icon_url):
        return _error("icon_link is invalid", 400)
    token = get_new_access_token()
    if not token.get("success"):
        return jsonify(token), 500
    try:
        with _client() as client:
            response = client.get(icon_url, headers=_bearer(token))
    except Exception as e:
        logger.error(f"Failed to get a tak.gov plugin icon: {e}")
        return _error(f"Failed to get icon: {e}", 502)
    if response.status_code != 200:
        return _error(f"Failed to get icon: {response.status_code}", 502)
    # Never serve tak.gov's bytes as HTML from this origin
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/") or content_type == "image/svg+xml":
        content_type = "application/octet-stream"
    proxied = Response(response.content, status=200, mimetype=content_type)
    proxied.headers["X-Content-Type-Options"] = "nosniff"
    return proxied


def _atak_version(value):
    """ "X.Y.Z" -> the version to file the plugin under, None for ATAK older than 5.5 (it doesn't
    query version-specific repositories). Raises ValueError for anything else."""
    match = _VERSION_RE.match(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("atak_version must be a version like 5.5.0")
    major, minor = int(match.group(1)), int(match.group(2))
    if major < 5 or (major == 5 and minor < 5):
        return None
    return bleach.clean(value.strip())


@tak_gov_link_blueprint.route("/api/takgov/plugin", methods=["POST"])
@roles_required("administrator")
def download_plugin():
    from raven.models.Packages import Packages

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    try:
        atak_version = _atak_version(data.get("atak_version"))
    except ValueError as e:
        return _error(str(e), 400)

    apk_url = data.get("apk_url")
    if not apk_url:
        return _error("plugin_url is required", 400)
    # The bearer token only ever goes to tak.gov's EUD API
    if not _is_eud_api_url(apk_url):
        return _error("plugin_url is invalid", 400)
    package_name = data.get("package_name")
    version = data.get("version")
    existing = db.session.query(Packages).filter_by(version=version, package_name=package_name).first()
    if existing is not None:
        return _error(f"Plugin {package_name} already exists", 400)

    token = get_new_access_token()
    if not token.get("success"):
        return jsonify(token), 500

    packages_folder = os.path.join(app.config.get("RAVEN_DATA_FOLDER"), "packages")
    try:
        with _client() as client:
            response = client.get(apk_url, headers=_bearer(token), follow_redirects=True)
            if response.status_code != 200:
                return jsonify(response.text), response.status_code
            filename = _content_disposition_filename(response) or secure_filename(
                f"{package_name or 'plugin'}-{version or ''}.apk"
            )
            if not filename or filename.startswith("."):
                filename = "plugin.apk"
            apk_bytes = response.content

            icon, icon_filename = None, None
            icon_url = data.get("icon_url")
            if icon_url and _is_eud_api_url(icon_url):
                icon_response = client.get(icon_url, headers=_bearer(token), follow_redirects=True)
                if icon_response.status_code == 200:
                    icon = icon_response.content
                    icon_filename = _content_disposition_filename(icon_response) or secure_filename(
                        f"{package_name or 'plugin'}.png"
                    )
    except Exception as e:
        logger.error(f"Failed to download {package_name} from tak.gov: {e}")
        logger.debug(traceback.format_exc())
        return _error(f"Failed to download plugin: {e}", 502)

    os.makedirs(packages_folder, exist_ok=True)
    path = os.path.join(packages_folder, filename)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(packages_folder):
        return _error("Invalid plugin file name", 400)
    with open(path, "wb") as f:
        f.write(apk_bytes)

    # ATAK checks the download against the hash in product.inf, so store the hash of what we serve
    apk_hash = hashlib.sha256(apk_bytes).hexdigest()
    claimed = data.get("apk_hash")
    if claimed and str(claimed).lower() != apk_hash:
        logger.warning(f"tak.gov's hash for {package_name} doesn't match the downloaded file; using the file's")

    try:
        package = Packages()
        package.platform = data.get("platform")
        package.apk_hash = apk_hash
        package.file_size = data.get("apk_size_bytes") or len(apk_bytes)
        package.package_name = package_name
        package.version = version
        package.description = data.get("description")
        package.name = data.get("display_name")
        package.revision_code = data.get("revision_code")
        package.os_requirement = data.get("os_requirement")
        package.plugin_type = data.get("apk_type")
        package.atak_version = atak_version
        package.icon = icon
        package.icon_filename = icon_filename
        package.file_name = filename
        package.publish_time = datetime.now(timezone.utc)
        db.session.add(package)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to add plugin {package_name} to the database: {e}")
        logger.debug(traceback.format_exc())
        return _error(f"Failed to add plugin to database: {e}", 500)

    create_product_infz(atak_version)
    return jsonify({"success": True})

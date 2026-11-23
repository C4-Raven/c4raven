"""Read-only administrators.

A user with the ``admin_viewer`` role sees the admin pages a full administrator
(the superadmin) has switched on for them, and nothing else. They can never
change anything: only the GET endpoints marked with ``admin_view(...)`` let them
in, every write endpoint keeps ``roles_required("administrator")``, and so do the
GETs that hand out secrets or files or have side effects (certificate passwords,
exports, profile file downloads, tak.gov calls that rotate the refresh token, the
security posture report that names admins still on the default password).

They never see an IP address either: ``redact_ips_for_viewer`` runs after every
request and blanks IPv4/IPv6 addresses in any text a read-only admin gets back. In
the admin responses ``admin_view`` lets them into it also blanks secrets: values
under password/token/secret-like keys, device-profile settings with such names, and
key=value secrets in log text.

The areas match the admin entries in the web UI's sidebar. Which ones a viewer
gets is stored per user in ``user.admin_view_areas`` (a JSON list), set by a
superadmin through PUT /api/user/admin_view.
"""

import functools
import ipaddress
import json
import re

from flask import g, jsonify, request
from flask_security import current_user

ADMIN_ROLE = "administrator"
VIEWER_ROLE = "admin_viewer"

# Keep in step with ADMIN_AREAS in the UI (src/adminAccess.ts).
ADMIN_AREAS = (
    "users",
    "groups",
    "server_requests",
    "plugin_updates",
    "device_profiles",
    "takgov",
    "federation_hub",
    "supporting_documents",
    "plugins",
    "meshtastic",
)

REDACTED_IP = "hidden"

_READ_METHODS = ("GET", "HEAD")


def parse_areas(raw) -> list[str]:
    """The stored areas as a clean list; anything unknown or malformed is dropped.

    Nothing stored yet (NULL) means every area: a new read-only admin sees all the admin
    pages until a superadmin switches some off. An empty list means none."""
    if raw is None:
        return list(ADMIN_AREAS)
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    return [a for a in ADMIN_AREAS if a in raw]


def is_full_admin(user) -> bool:
    return bool(user and user.is_authenticated and user.has_role(ADMIN_ROLE))


def is_admin_viewer(user) -> bool:
    """A read-only admin: has the viewer role and is not also a full administrator."""
    return bool(
        user
        and user.is_authenticated
        and user.has_role(VIEWER_ROLE)
        and not user.has_role(ADMIN_ROLE)
    )


def viewer_areas(user) -> list[str]:
    if not is_admin_viewer(user):
        return []
    return parse_areas(getattr(user, "admin_view_areas", None))


def admin_view(*areas: str):
    """Let full administrators in, and read-only admins too when the request is a read
    and the superadmin has switched on at least one of ``areas`` for them."""
    for area in areas:
        if area not in ADMIN_AREAS:
            raise ValueError(f"Unknown admin area {area!r}")

    def decorator(f):
        @functools.wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user or not current_user.is_authenticated:
                return jsonify({"success": False, "error": "Authentication required"}), 401
            if current_user.has_role(ADMIN_ROLE):
                return f(*args, **kwargs)
            if (
                request.method in _READ_METHODS
                and is_admin_viewer(current_user)
                and set(areas) & set(viewer_areas(current_user))
            ):
                # Sorting by an IP column would leak the addresses' order
                if "ip" in (request.args.get("sort_by") or "").lower():
                    return jsonify({"success": False, "error": "You do not have permission"}), 403
                # Tells the after_request hook to blank secrets in this admin response too
                g.admin_view_response = True
                return f(*args, **kwargs)
            return jsonify({"success": False, "error": "You do not have permission"}), 403

        return wrapped

    return decorator


# Candidates only: each match is checked before it is replaced, so times like 12:34:56 and
# MAC addresses are left alone. An address glued to a letter or underscore ("x10.0.0.5",
# "%2F10.0.0.5") still counts; so does one written with leading zeros.
_IPV4 = r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)"
_IPV6 = (
    r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}"
    r"(?:(?:\d{1,3}\.){3}\d{1,3}|[0-9A-Fa-f]{0,4})"
    r"(?:%[\w.-]+)?(?![\w:]|\.\d)"
)
_CANDIDATE = re.compile(f"{_IPV6}|{_IPV4}")

# Keys whose values are never addresses, even when they look like one (e.g. "5.6.0.123").
_SKIP_KEYS = re.compile(r"version$", re.IGNORECASE)


def _is_ip(candidate: str) -> bool:
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass
    parts = candidate.split(".")
    # ipaddress refuses leading zeros ("010.000.000.005"); a reader wouldn't
    return len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts)


def _replace(match: re.Match) -> str:
    text = match.group(0)
    return REDACTED_IP if _is_ip(text.split("%", 1)[0]) else text


def redact_text(text: str) -> str:
    return _CANDIDATE.sub(_replace, text)


# Key names whose values are secrets: federation tokens in a hub policy, passwords,
# private keys. Matched anywhere in the name and in any case (keystorePassword, accessToken).
_SECRET_NAME = re.compile(
    r"pass(word|wd|phrase)|secret|token|jwt|private_?key|api_?key|credential|authori[sz]ation|cookie",
    re.IGNORECASE,
)
# Settings stored as {"key": "caPassword", "value": "..."} (device profiles): the secret is
# named by a sibling field, not by the JSON key itself.
_SETTING_PAIRS = (("key", "value"), ("preference_key", "preference_value"), ("name", "value"))

# key=value or "key": "value" pairs in plain text (log lines), and bearer credentials
_TEXT_SECRET = re.compile(
    r"(?i)((?:pass(?:word|wd|phrase)|secret|token|jwt|api_?key|authorization)[\w-]*"
    r"[\"']?\s*[:=]\s*[\"']?(?:(?:bearer|basic)\s+)?)([^\s\"',;&]+)"
    r"|((?:bearer|basic)\s+)([A-Za-z0-9._~+/=-]+)"
)


def _blank(value):
    if isinstance(value, str):
        return REDACTED_IP if value else value
    if isinstance(value, list):
        return [_blank(v) for v in value]
    if isinstance(value, dict):
        return {k: _blank(v) for k, v in value.items()}
    return value  # numbers, booleans, null say nothing secret


def redact_secrets(value, key: str | None = None):
    """A copy of ``value`` with everything under a secret-named key blanked."""
    if key and _SECRET_NAME.search(key):
        return _blank(value)
    if isinstance(value, list):
        return [redact_secrets(v, key) for v in value]
    if isinstance(value, dict):
        out = {k: redact_secrets(v, k) for k, v in value.items()}
        for name_field, value_field in _SETTING_PAIRS:
            name = value.get(name_field)
            if isinstance(name, str) and value_field in out and _SECRET_NAME.search(name):
                out[value_field] = _blank(out[value_field])
        return out
    return value


def redact_text_secrets(text: str) -> str:
    def sub(m):
        if m.group(1):
            return m.group(1) + REDACTED_IP
        return m.group(3) + REDACTED_IP

    return _TEXT_SECRET.sub(sub, text)


def redact(value, key: str | None = None):
    """A copy of ``value`` (decoded JSON) with every IP address replaced, in keys too."""
    if isinstance(value, str):
        if key and _SKIP_KEYS.search(key):
            return value
        return redact_text(value)
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, dict):
        return {redact_text(k): redact(v, k) for k, v in value.items()}
    return value


def _is_textual(response) -> bool:
    mimetype = (response.mimetype or "").lower()
    return (
        response.is_json
        or mimetype.startswith("text/")
        or mimetype in ("application/xml", "application/javascript")
        or mimetype.endswith("+xml")
    )


def redact_ips_for_viewer(response):
    """after_request hook for read-only admins: blank IP addresses in every text response,
    and secrets too in the admin responses they are let into (``admin_view``).

    Fails closed: if a body that should be filtered can't be, the viewer gets an error."""
    try:
        if not is_admin_viewer(current_user):
            return response
    except Exception:
        return response
    if not _is_textual(response):
        return response  # files, images, video: nothing a text filter can do
    admin_response = bool(getattr(g, "admin_view_response", False))
    try:
        if response.direct_passthrough:
            # A streamed text body: read it in so it can be filtered like the rest
            response.direct_passthrough = False
        raw = response.get_data()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return response  # not really text (e.g. image bytes labelled text/html)
        data = response.get_json(silent=True) if response.is_json else None
        if data is None:
            text = redact_text(text)
            if admin_response:
                text = redact_text_secrets(text)
            response.set_data(text)
            return response
        data = redact(data)
        if admin_response:
            data = redact_secrets(data)
        response.set_data(json.dumps(data))
        return response
    except Exception:
        response.set_data(json.dumps({"success": False, "error": "Response withheld"}))
        response.mimetype = "application/json"
        response.status_code = 500
        return response

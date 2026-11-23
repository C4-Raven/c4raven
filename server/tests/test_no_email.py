"""The server sends no email: email features are off and can't be turned on, email-2FA users are
moved to an authenticator app, and nothing imports a mail extension. Also: AIS is gone."""

import os
import pickle
import subprocess
import sys

import pyotp
import yaml
from flask_security import hash_password
from sqlalchemy import select

import raven
from raven.app import create_app
from raven.defaultconfig import DefaultConfig
from raven.extensions import db
from raven.no_email import (
    TF_SETUP_REQUIRED,
    NoEmailMailUtil,
    RavenCodeTfPlugin,
    reset_email_two_factor,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(raven.__file__)))
RAVEN_DIR = os.path.dirname(os.path.abspath(raven.__file__))

OLD_EMAIL_CONFIG = {
    "RAVEN_ENABLE_EMAIL": True,
    "MAIL_SERVER": "smtp.example.com",
    "MAIL_USERNAME": "raven@example.com",
    "MAIL_PASSWORD": "secret",
    "RAVEN_EMAIL_DOMAIN_WHITELIST": ["example.com"],
    "SECURITY_REGISTERABLE": True,
    "SECURITY_CONFIRMABLE": True,
    "SECURITY_RECOVERABLE": True,
    "SECURITY_PASSWORDLESS": True,
    "SECURITY_UNIFIED_SIGNIN": True,
    "SECURITY_CHANGE_EMAIL": True,
    "SECURITY_USERNAME_RECOVERY": True,
    "SECURITY_TWO_FACTOR_RESCUE_MAIL": "admin@example.com",
    "SECURITY_TWO_FACTOR_ENABLED_METHODS": ["authenticator", "email"],
    "RAVEN_AISHUB_USERNAME": "someone",
    "RAVEN_AIS_GROUP": "AIS",
}


def _app_with_old_email_config(app):
    """A second app on the test's database, from a config.yml that still turns email on."""
    path = os.path.join(app.config["RAVEN_DATA_FOLDER"], "config.yml")
    with open(path) as f:
        config = yaml.safe_load(f)
    config.update(OLD_EMAIL_CONFIG)
    with open(path, "w") as f:
        yaml.safe_dump(config, f)
    new_app = create_app()
    new_app.config.update(TESTING=True)
    return new_app


def _make_user(app, username, method=None, secret=None):
    with app.app_context():
        ds = app.security.datastore
        ds.find_or_create_role("user")
        user = ds.create_user(
            username=username, password=hash_password("Password123!"), roles=["user"]
        )
        user.tf_primary_method = method
        user.tf_totp_secret = secret
        db.session.commit()


def _user(app, username):
    from raven.models.user import User

    with app.app_context():
        user = db.session.execute(select(User).filter_by(username=username)).scalar_one()
        db.session.expunge(user)
        return user


def _login(app, client, username, password="Password123!"):
    r = client.get("/api/login", headers={"Accept": "application/json"})
    csrf = r.json["response"]["csrf_token"]
    headers = {"X-CSRFToken": csrf}
    return (
        client.post(
            "/api/login", json={"username": username, "password": password}, headers=headers
        ),
        headers,
    )


# --------------------------------------------------------------------------------- settings


def test_defaults_have_no_email_settings():
    for key in dir(DefaultConfig):
        assert not key.startswith(("MAIL_", "RAVEN_EMAIL_", "RAVEN_AIS")), key
    assert not hasattr(DefaultConfig, "RAVEN_ENABLE_EMAIL")
    assert not hasattr(DefaultConfig, "SECURITY_TWO_FACTOR_RESCUE_MAIL")
    assert DefaultConfig.SECURITY_TWO_FACTOR_ENABLED_METHODS == ["authenticator"]


def test_old_email_config_has_no_effect(app):
    new_app = _app_with_old_email_config(app)
    config = new_app.config

    for key in ("REGISTERABLE", "CONFIRMABLE", "RECOVERABLE", "PASSWORDLESS", "UNIFIED_SIGNIN"):
        assert config[f"SECURITY_{key}"] is False, key
    assert config["SECURITY_CHANGE_EMAIL"] is False
    assert config["SECURITY_USERNAME_RECOVERY"] is False
    assert config["SECURITY_TWO_FACTOR_RESCUE_EMAIL"] is False
    assert config["SECURITY_TWO_FACTOR_ENABLED_METHODS"] == ["authenticator"]
    assert "email" not in config["SECURITY_US_ENABLED_METHODS"]
    for key in config:
        assert not key.startswith(("MAIL_", "RAVEN_EMAIL_")), key
    assert "RAVEN_ENABLE_EMAIL" not in config
    assert config.get("SECURITY_TWO_FACTOR_RESCUE_MAIL") != "admin@example.com"
    identity = config["SECURITY_USER_IDENTITY_ATTRIBUTES"]
    assert [list(attr)[0] for attr in identity] == ["username"]

    # No mail extension, and Flask-Security's mail utility is the one that never sends
    assert "mailman" not in new_app.extensions and "mail" not in new_app.extensions
    assert isinstance(new_app.security.mail_util, NoEmailMailUtil)


def test_email_endpoints_do_not_exist(app):
    new_app = _app_with_old_email_config(app)
    rules = {rule.rule for rule in new_app.url_map.iter_rules()}
    for path in (
        "/api/register",
        "/api/confirm",
        "/api/reset",
        "/api/password/reset",
        "/api/us-signin",
        "/api/us-signin/send-code",
        "/api/login/send-code",
        "/api/change-email",
        "/api/recover-username",
    ):
        assert path not in rules, path
    assert not any(r.startswith(("/api/reset", "/api/confirm", "/api/register")) for r in rules)

    client = new_app.test_client()
    for path in ("/api/register", "/api/reset", "/api/password/reset", "/api/confirm"):
        assert client.post(path, json={"email": "a@example.com"}).status_code == 404, path
        assert client.get(path).status_code == 404, path

    # 2FA itself is still there
    assert "/api/tf-setup" in rules and "/api/tf-validate" in rules


def test_mail_util_never_sends(app, caplog):
    with app.app_context():
        util = app.security.mail_util
        caplog.set_level("DEBUG", logger="Raven")
        assert (
            util.send_mail("t", "Subject here", "a@example.com", "b@example.com", "body", None)
            is None
        )
        assert util.validate("Someone@Example.com").endswith("@example.com")


def test_nothing_imports_a_mail_extension():
    for root, _, files in os.walk(RAVEN_DIR):
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    text = f.read()
                assert "flask_mailman" not in text and "flask_mail " not in text, name
                assert "from flask_mail" not in text and "import flask_mail" not in text, name

    with open(os.path.join(REPO, "pyproject.toml")) as f:
        pyproject = f.read()
    assert "flask-mailman" not in pyproject and "aiscot" not in pyproject

    # Importing the app (and its Flask-Security setup) loads no mail extension
    env = dict(os.environ)
    code = (
        "import sys, raven.app, raven.no_email, raven.eud_handler.EudHandler; "
        "bad = [m for m in sys.modules if m.split('.')[0] in ('flask_mailman', 'flask_mail')]; "
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_plugin_is_installed(app):
    impls = app.config["SECURITY_TWO_FACTOR_IMPLEMENTATIONS"]
    assert impls["code"] == "raven.no_email.RavenCodeTfPlugin"
    assert "webauthn" in impls
    assert isinstance(app.security.two_factor_plugins._tf_impls["code"], RavenCodeTfPlugin)


# --------------------------------------------------------------------------------- email 2FA


def test_email_two_factor_users_are_reset(app):
    authenticator_secret = app.security.totp_factory.generate_totp_secret()
    _make_user(app, "by_email", "email", "emailsecret")
    _make_user(app, "by_mail", "mail", "mailsecret")
    _make_user(app, "by_app", "authenticator", authenticator_secret)
    _make_user(app, "no_tfa")

    with app.app_context():
        assert reset_email_two_factor() == 2
        assert reset_email_two_factor() == 0  # idempotent

    for name in ("by_email", "by_mail"):
        user = _user(app, name)
        assert user.tf_primary_method is None
        assert user.tf_totp_secret == TF_SETUP_REQUIRED
    assert _user(app, "by_app").tf_primary_method == "authenticator"
    assert _user(app, "by_app").tf_totp_secret == authenticator_secret
    assert _user(app, "no_tfa").tf_totp_secret is None

    from raven.models.AuditEvent import AuditEvent

    with app.app_context():
        rows = db.session.execute(select(AuditEvent).filter_by(action="tfa_change")).scalars().all()
        assert sorted(r.username for r in rows) == ["by_email", "by_mail"]


def test_reset_user_must_set_up_authenticator_at_sign_in(app):
    _make_user(app, "was_email", "email", "emailsecret")
    with app.app_context():
        reset_email_two_factor()
    assert not app.config.get("SECURITY_TWO_FACTOR_REQUIRED")

    client = app.test_client()
    r, headers = _login(app, client, "was_email")
    assert r.status_code == 200, r.json
    assert r.json["response"]["tf_required"] is True
    assert r.json["response"]["tf_state"] == "setup_from_login"
    # Not signed in yet
    assert client.get("/api/me", headers={"Accept": "application/json"}).status_code in (
        302,
        401,
        403,
    )

    # Email is not offered
    r = client.post("/api/tf-setup", json={"setup": "email"}, headers=headers)
    assert r.status_code == 400

    r = client.post("/api/tf-setup", json={"setup": "authenticator"}, headers=headers)
    assert r.status_code == 200, r.json
    with client.session_transaction() as session:
        secret = session["tf_totp_secret"]
    code = app.security.totp_factory.generate_totp_password(secret)
    r = client.post("/api/tf-validate", json={"code": code}, headers=headers)
    assert r.status_code == 200, r.json

    user = _user(app, "was_email")
    assert user.tf_primary_method == "authenticator"
    assert user.tf_totp_secret not in (None, TF_SETUP_REQUIRED)

    # Next sign-in asks for the authenticator code, as for any authenticator user
    client.get("/api/logout")
    client2 = app.test_client()
    r, headers = _login(app, client2, "was_email")
    assert r.json["response"]["tf_required"] is True
    assert r.json["response"].get("tf_state") == "ready"
    assert r.json["response"]["tf_method"] == "authenticator"


def test_authenticator_user_still_asked_for_code(app):
    secret_json = app.security.totp_factory.generate_totp_secret()
    _make_user(app, "app_user", "authenticator", secret_json)
    client = app.test_client()
    r, headers = _login(app, client, "app_user")
    assert r.json["response"]["tf_required"] is True
    assert r.json["response"]["tf_state"] == "ready"
    code = app.security.totp_factory.generate_totp_password(secret_json)
    r = client.post("/api/tf-validate", json={"code": code}, headers=headers)
    assert r.status_code == 200, r.json


def test_user_without_two_factor_signs_straight_in(app):
    _make_user(app, "plain")
    r, _ = _login(app, app.test_client(), "plain")
    assert r.status_code == 200
    assert not r.json["response"].get("tf_required")


def test_two_factor_required_still_applies(app):
    app.config["SECURITY_TWO_FACTOR_REQUIRED"] = True
    _make_user(app, "needs_tfa")
    r, _ = _login(app, app.test_client(), "needs_tfa")
    assert r.json["response"]["tf_required"] is True
    assert r.json["response"]["tf_state"] == "setup_from_login"


# --------------------------------------------------------------------------------- AIS


def test_ais_is_gone():
    assert not os.path.exists(os.path.join(REPO, "vendor", "aiscot-7.0.10-py3-none-any.whl"))
    with open(os.path.join(REPO, "poetry.lock")) as f:
        lock = f.read()
    assert 'name = "aiscot"' not in lock and 'name = "flask-mailman"' not in lock
    assert not any(job["id"] == "ais" for job in DefaultConfig.JOBS)


def test_stored_ais_job_row_is_dropped(tmp_path):
    """An old database may hold the AIS job, whose function no longer exists: loading the job
    store drops it instead of failing."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from raven.sql_jobstore import SQLJobStore

    url = f"sqlite:///{tmp_path}/jobs.db"
    scheduler = BackgroundScheduler()
    store = SQLJobStore(url=url)
    scheduler.add_jobstore(store)
    scheduler.add_job(pyotp.random_base32, "interval", minutes=1, id="ais")
    scheduler.add_job(pyotp.random_base32, "interval", minutes=1, id="kept")
    scheduler.start(paused=True)
    try:
        # Point the stored row at the AIS fetcher that was removed
        with store.engine.begin() as connection:
            row = connection.execute(store.jobs_t.select().where(store.jobs_t.c.id == "ais")).one()
            state = pickle.loads(row.job_state)
            state["func"] = "raven.blueprints.scheduled_jobs:get_aishub_data"
            connection.execute(
                store.jobs_t.update()
                .where(store.jobs_t.c.id == "ais")
                .values(job_state=pickle.dumps(state, store.pickle_protocol))
            )
        assert [job.id for job in store.get_all_jobs()] == ["kept"]
        assert store.lookup_job("ais") is None
    finally:
        scheduler.shutdown(wait=False)

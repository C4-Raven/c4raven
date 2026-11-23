"""Shared fixtures: a CLI-mode app on a throwaway database and data folder.

RAVEN_DATA_FOLDER (and the CA/upload folders) point at a temporary folder before raven is
imported, so DefaultConfig and the module-level service apps (cot_parser, eud_handler) never
touch the server's own data folder (see live_data_folder).
"""

import ast
import atexit
import os
import shutil
import tempfile
from pathlib import Path

TEST_DATA_FOLDER = tempfile.mkdtemp(prefix="raven-tests-")
atexit.register(shutil.rmtree, TEST_DATA_FOLDER, ignore_errors=True)
os.environ["RAVEN_DATA_FOLDER"] = TEST_DATA_FOLDER
os.environ["RAVEN_CA_FOLDER"] = os.path.join(TEST_DATA_FOLDER, "ca")
os.environ["UPLOAD_FOLDER"] = os.path.join(TEST_DATA_FOLDER, "uploads")

import pytest  # noqa: E402
import sqlalchemy.exc  # noqa: E402
import yaml  # noqa: E402
from flask_security import hash_password  # noqa: E402

from raven.app import create_app  # noqa: E402
from raven.defaultconfig import DefaultConfig  # noqa: E402
from raven.extensions import db, logger  # noqa: E402
from raven.models import import_all_models  # noqa: E402

# Map every model up front, so a test module's own minimal app never meets a relationship whose
# target class nobody imported ("expression 'RBLine' failed to locate a name")
import_all_models()


def live_data_folder() -> str:
    """The data folder a real server uses when RAVEN_DATA_FOLDER is unset: the fallback of the
    `_DATA_FOLDER = os.getenv("RAVEN_DATA_FOLDER", <fallback>)` line in raven/defaultconfig.py.

    Read from the source because importing defaultconfig without the variable would create
    folders under the live one; this way the tests follow any change to that default.
    """
    import raven.defaultconfig as defaultconfig

    with open(defaultconfig.__file__, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_DATA_FOLDER" for t in node.targets)
            and isinstance(node.value, ast.Call)
            and len(node.value.args) == 2
        ):
            fallback = ast.Expression(node.value.args[1])
            value = eval(compile(fallback, defaultconfig.__file__, "eval"), {"os": os, "Path": Path})
            return os.path.realpath(os.path.expanduser(value))
    raise AssertionError("raven/defaultconfig.py no longer defines _DATA_FOLDER via os.getenv")


live_data_folder.__test__ = False


def test_database_uri(tmp_path) -> str:
    return os.environ.get("RAVEN_TEST_DATABASE_URI") or f"sqlite:///{tmp_path}/test.db"


test_database_uri.__test__ = False


def is_test_database(uri) -> bool:
    test_uri = os.environ.get("RAVEN_TEST_DATABASE_URI")
    return bool(uri) and ((test_uri and uri == test_uri) or uri.startswith("sqlite"))


is_test_database.__test__ = False


class AuthActions:
    """A logged-in administrator session on the test client (account created on first use)."""

    def __init__(self, app, client, username="TestUser", password="TestPass"):
        self.app, self.client = app, client
        self.username, self.password = username, password
        self.headers = {}  # carries the CSRF token once logged in
        self._ensure_admin_account()
        self.login()

    def _ensure_admin_account(self):
        datastore = self.app.security.datastore
        with self.app.app_context():
            datastore.find_or_create_role("administrator")
            datastore.create_user(
                username=self.username, password=hash_password(self.password), roles=["administrator"]
            )
            try:
                db.session.commit()
            except sqlalchemy.exc.IntegrityError:
                db.session.rollback()
                logger.warning(f"{self.username} already exists")

    def login(self):
        r = self.client.get("/api/login", headers={"Accept": "application/json"})
        self.headers["X-CSRFToken"] = r.json["response"]["csrf_token"]
        return self.client.post(
            "/api/login",
            json={"username": self.username, "password": self.password},
            headers=self.headers,
        )

    def logout(self):
        return self.client.get("/api/logout")

    def get(self, path, headers=None, json=None):
        return self.client.get(
            path,
            headers=self.headers if headers is None else headers,
            json={} if json is None else json,
        )

    def post(self, path, headers=None):
        return self.client.post(path, headers=self.headers if headers is None else headers)


@pytest.fixture
def app(tmp_path, monkeypatch):
    uri = test_database_uri(tmp_path)
    data_folder = tmp_path / "data"
    data_folder.mkdir()

    real_folder = DefaultConfig.RAVEN_DATA_FOLDER
    for key in dir(DefaultConfig):
        value = getattr(DefaultConfig, key)
        if key.isupper() and isinstance(value, str) and value.startswith(real_folder):
            monkeypatch.setattr(DefaultConfig, key, str(data_folder) + value[len(real_folder) :])
    monkeypatch.setattr(DefaultConfig, "SQLALCHEMY_DATABASE_URI", uri)

    with open(data_folder / "config.yml", "w") as f:
        yaml.safe_dump(
            {
                "SQLALCHEMY_DATABASE_URI": uri,
                "RAVEN_DATA_FOLDER": str(data_folder),
                "UPLOAD_FOLDER": str(data_folder / "uploads"),
                "RAVEN_CA_FOLDER": str(data_folder / "ca"),
            },
            f,
        )

    handlers_before = list(logger.handlers)
    app = create_app()

    if app.config["SQLALCHEMY_DATABASE_URI"] != uri or not is_test_database(
        app.config["SQLALCHEMY_DATABASE_URI"]
    ):
        pytest.exit(
            "Refusing to run: the test app is configured with a database that isn't a test "
            f"database. ({app.config['SQLALCHEMY_DATABASE_URI']})",
            returncode=1,
        )
    if not os.path.realpath(app.config["RAVEN_DATA_FOLDER"]).startswith(os.path.realpath(tmp_path)):
        pytest.exit("Refusing to run: RAVEN_DATA_FOLDER isn't the test's temporary folder.")

    app.config.update(TESTING=True, PRESERVE_CONTEXT_ON_EXCEPTION=False)
    if uri.startswith("sqlite"):
        with app.app_context():
            import_all_models()
            db.create_all()

    yield app

    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    for handler in list(logger.handlers):
        if handler not in handlers_before:
            logger.removeHandler(handler)
            handler.close()


@pytest.fixture
def client(app):
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["Authorization"] = "redacted"
        yield client


@pytest.fixture
def auth(app, client):
    return AuthActions(app, client)

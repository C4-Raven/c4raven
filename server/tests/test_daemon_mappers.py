"""Each daemon builds its own app and must be able to resolve every model relationship on
its own. Run in a fresh interpreter: inside the test session every model is already imported,
which hides a missing import."""

import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = """
import sys
from sqlalchemy.orm import configure_mappers
from {module} import create_app
create_app()
configure_mappers()
print("ok")
"""


@pytest.mark.parametrize("module", ["raven.controllers.federation_hub_daemon", "raven.cot_parser.cot_parser"])
def test_daemon_app_configures_all_mappers(module, tmp_path):
    env = dict(os.environ, PYTHONPATH=REPO, RAVEN_DATA_FOLDER=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT.format(module=module)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().endswith("ok")


# Builds the database in one process; FRESH_RESET then runs the reset in a new one
RESET_SCRIPT = """
from flask import Flask
from raven.extensions import db
app = Flask("t")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///t.db"
db.init_app(app)
with app.app_context():
    from raven.models import import_all_models
    import_all_models()
    db.create_all()
"""

FRESH_RESET = """
from flask import Flask
from raven.extensions import db
app = Flask("t")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///t.db"
db.init_app(app)
with app.app_context():
    from raven.no_email import reset_email_two_factor
    reset_email_two_factor()
    from sqlalchemy.orm import configure_mappers
    configure_mappers()
print("ok")
"""


def test_email_2fa_reset_works_in_a_fresh_process(tmp_path):
    # raven runs reset_email_two_factor() early in startup, before the scheduler imports every
    # model; it must load them itself or the User mapper cannot find Role and startup dies
    env = dict(os.environ, PYTHONPATH=REPO, RAVEN_DATA_FOLDER=str(tmp_path))
    for script in (RESET_SCRIPT, FRESH_RESET):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().endswith("ok")

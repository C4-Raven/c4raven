"""The purge_logs scheduled job empties current logs and deletes rotated and archived ones."""

import os
import types

from raven.blueprints import scheduled_jobs


def test_purge_logs(app, tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    (logs / "archive").mkdir(parents=True)
    (logs / "raven.log").write_text("current\n")
    (logs / "cot_parser.log").write_text("current\n")
    (logs / "raven.log.2026-11-15").write_text("old\n")
    (logs / "eud_handler_ssl.log.3").write_text("old\n")
    (logs / "archive" / "stdout-raven-legacy.log.2026-05-31").write_text("old\n")
    (logs / "notes.txt").write_text("not a log\n")
    outside = tmp_path / "elsewhere.log"
    outside.write_text("keep\n")
    os.symlink(outside, logs / "linked.log")

    app.config["RAVEN_DATA_FOLDER"] = str(tmp_path)
    monkeypatch.setattr(scheduled_jobs, "apscheduler", types.SimpleNamespace(app=app))
    with app.app_context():
        scheduled_jobs.purge_logs()

    assert (logs / "raven.log").read_text() == ""
    assert (logs / "cot_parser.log").read_text() == ""
    assert not (logs / "raven.log.2026-11-15").exists()
    assert not (logs / "eud_handler_ssl.log.3").exists()
    assert os.listdir(logs / "archive") == []
    assert (logs / "notes.txt").read_text() == "not a log\n"
    # A symlink is never followed or removed
    assert outside.read_text() == "keep\n" and os.path.islink(logs / "linked.log")


def test_purge_logs_is_registered_paused():
    from raven.app import ADDED_SCHEDULER_JOB_IDS
    from raven.blueprints.raven_api.scheduler_api import JOB_DESCRIPTIONS
    from raven.defaultconfig import DefaultConfig

    job = next(j for j in DefaultConfig.JOBS if j["id"] == "purge_logs")
    assert job["next_run_time"] is None
    assert "purge_logs" in ADDED_SCHEDULER_JOB_IDS
    assert "purge_logs" in JOB_DESCRIPTIONS

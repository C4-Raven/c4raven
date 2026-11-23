"""Restarting the Federation Hub bridge from the web UI (raven/fedhub_bridge_control.py)."""

import json

import pytest

from raven import fedhub_bridge_control as ctl

# The minimal fedhub API app and a logged-in admin client
from tests.test_fedhub_admin_cert import admin, app  # noqa: F401


@pytest.fixture(autouse=True)
def no_restart_trigger(monkeypatch):
    """Tests never ask this machine's systemd; the trigger tests set their own."""
    monkeypatch.setattr(ctl, "restart_trigger_path", lambda: None)


class _Method:
    def __init__(self, tag):
        self.delivery_tag = tag


class FakeChannel:
    def __init__(self, messages):
        self.messages = messages
        self.acked = []

    def queue_declare(self, **kwargs):
        class R:
            class method:
                consumer_count = 0
        return R

    def consume(self, queue):
        for i, body in enumerate(self.messages):
            yield _Method(i), None, body

    def basic_ack(self, tag):
        self.acked.append(tag)


class FakeConnection:
    def __init__(self, channel):
        self._channel = channel

    def channel(self):
        return self._channel

    def close(self):
        pass


class Exited(BaseException):
    pass


def test_control_loop_acks_then_exits_on_restart(monkeypatch):
    channel = FakeChannel([b"not json", json.dumps({"action": "noop"}).encode(),
                           json.dumps({"action": "restart", "by": "admin"}).encode()])
    monkeypatch.setattr(ctl, "_connection", lambda cfg: FakeConnection(channel))
    codes = []

    def fake_exit(code):
        codes.append(code)
        raise Exited

    with pytest.raises(Exited):
        ctl.run_control_loop({}, exit_fn=fake_exit)

    # Every message acked, the restart before exiting (or it would be redelivered)
    assert channel.acked == [0, 1, 2]
    assert codes == [ctl.RESTART_EXIT_CODE] and ctl.RESTART_EXIT_CODE != 0


def test_status_reports_whether_the_bridge_listens(admin, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ctl, "bridge_running", lambda cfg: True)
    assert admin.get("/api/fedhub/bridge").json == {"running": True, "can_start": False}
    monkeypatch.setattr(ctl, "bridge_running", lambda cfg: False)
    assert admin.get("/api/fedhub/bridge").json == {"running": False, "can_start": False}


def test_restart_sends_request(admin, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(ctl, "request_restart", lambda cfg, by=None: calls.append(by) or True)
    r = admin.post("/api/fedhub/bridge/restart")
    assert r.status_code == 200 and r.json["success"] is True
    assert calls == ["admin"]


def test_restart_when_bridge_is_down_gives_the_command(admin, monkeypatch):  # noqa: F811
    monkeypatch.setattr(ctl, "request_restart", lambda cfg, by=None: False)
    r = admin.post("/api/fedhub/bridge/restart")
    assert r.status_code == 409
    assert "systemctl restart fedhub_bridge" in r.json["error"]


def test_rabbitmq_down_is_a_503(admin, monkeypatch):  # noqa: F811
    def boom(*a, **k):
        raise ConnectionError("no broker")

    monkeypatch.setattr(ctl, "request_restart", boom)
    monkeypatch.setattr(ctl, "bridge_running", boom)
    assert admin.post("/api/fedhub/bridge/restart").status_code == 503
    assert admin.get("/api/fedhub/bridge").status_code == 503


def test_requires_an_administrator(app):  # noqa: F811
    client = app.test_client()
    assert client.get("/api/fedhub/bridge").status_code in (401, 403)
    assert client.post("/api/fedhub/bridge/restart").status_code in (400, 401, 403)


# -- the systemd restart trigger (fedhub_bridge-restart.path) -----------------


class Show:
    def __init__(self, stdout):
        self.stdout = stdout


def _show(monkeypatch, stdout=None, error=None):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if error:
            raise error
        return Show(stdout)

    monkeypatch.setattr(ctl.subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    "stdout, expected",
    [
        ("ActiveState=active\nPaths=/crw/raven/data/run/restart-fedhub-bridge (PathChanged)\n",
         "/crw/raven/data/run/restart-fedhub-bridge"),
        # Not installed: systemd still answers, with an inactive unit and no paths
        ("ActiveState=inactive\nPaths=\n", None),
        ("ActiveState=active\nPaths=/some/where (PathExists)\n", None),
        ("ActiveState=active\nPaths=relative (PathChanged)\n", None),
        ("", None),
    ],
)
def test_trigger_path_comes_from_systemd(monkeypatch, stdout, expected):
    monkeypatch.undo()  # the autouse stub
    calls = _show(monkeypatch, stdout)
    assert ctl.restart_trigger_path() == expected
    assert calls[0][:3] == ["systemctl", "show", ctl.TRIGGER_UNIT]


def test_no_systemctl_means_no_trigger(monkeypatch):
    monkeypatch.undo()
    _show(monkeypatch, error=FileNotFoundError("systemctl"))
    assert ctl.restart_trigger_path() is None


def test_restart_uses_the_trigger_even_when_the_bridge_is_down(admin, monkeypatch, tmp_path):  # noqa: F811
    trigger = tmp_path / "run" / "restart-fedhub-bridge"
    monkeypatch.setattr(ctl, "restart_trigger_path", lambda: str(trigger))
    monkeypatch.setattr(ctl, "request_restart", lambda *a, **k: pytest.fail("asked the bridge instead"))
    monkeypatch.setattr(ctl, "bridge_running", lambda cfg: False)

    assert admin.get("/api/fedhub/bridge").json == {"running": False, "can_start": True}
    r = admin.post("/api/fedhub/bridge/restart")
    assert r.status_code == 200 and r.json["success"] is True
    body = json.loads(trigger.read_text())
    assert body["action"] == "restart" and body["by"] == "admin"


def test_unwritable_trigger_falls_back_to_asking_the_bridge(admin, monkeypatch, tmp_path):  # noqa: F811
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(ctl, "restart_trigger_path", lambda: str(blocker / "restart-fedhub-bridge"))
    calls = []
    monkeypatch.setattr(ctl, "request_restart", lambda cfg, by=None: calls.append(by) or True)
    assert admin.post("/api/fedhub/bridge/restart").status_code == 200
    assert calls == ["admin"]

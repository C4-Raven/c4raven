"""Restart the Federation Hub bridge from the web UI, without sudo.

The bridge (fedhub_bridge.service) runs as its own process. It consumes
CONTROL_QUEUE. The web API asks it to restart by publishing a message there.
The bridge acks it and exits with status 1, and systemd's Restart=on-failure
starts it again a few seconds later. Whether the bridge is up is whether the
queue has a consumer.

That can't help a bridge that has stopped. Where the updater installed
fedhub_bridge-restart.path (a root-owned systemd path unit), the web app writes
the file it watches instead, and systemd restarts the bridge whatever state it
is in. The app learns the file from systemd itself, so there's nothing to
configure, and it gains no privilege beyond causing that one restart.
"""

import json
import logging
import os
import subprocess
import time
import traceback

import pika

CONTROL_QUEUE = "fedhub_bridge_control"
# A request nobody picks up within this time is dropped, so a click while the
# bridge is down can't restart it again later, after it came back by itself.
_QUEUE_ARGS = {"x-message-ttl": 30_000}
RESTART_EXIT_CODE = 1
TRIGGER_UNIT = "fedhub_bridge-restart.path"

log = logging.getLogger("Raven")


def _connection(cfg) -> pika.BlockingConnection:
    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=cfg.get("RAVEN_RABBITMQ_SERVER_ADDRESS"),
            credentials=pika.PlainCredentials(
                cfg.get("RAVEN_RABBITMQ_USERNAME"), cfg.get("RAVEN_RABBITMQ_PASSWORD")
            ),
        )
    )


def declare(channel) -> int:
    """Declares the control queue (idempotent) and returns its consumer count."""
    return channel.queue_declare(
        queue=CONTROL_QUEUE, durable=False, arguments=_QUEUE_ARGS
    ).method.consumer_count


def bridge_running(cfg) -> bool:
    conn = _connection(cfg)
    try:
        return declare(conn.channel()) > 0
    finally:
        conn.close()


def request_restart(cfg, requested_by: str | None = None) -> bool:
    """Asks the bridge to restart. False when no bridge is listening."""
    conn = _connection(cfg)
    try:
        channel = conn.channel()
        if declare(channel) == 0:
            return False
        channel.basic_publish(
            exchange="",
            routing_key=CONTROL_QUEUE,
            body=json.dumps({"action": "restart", "by": requested_by, "at": time.time()}),
        )
        return True
    finally:
        conn.close()


def restart_trigger_path() -> str | None:
    """The file fedhub_bridge-restart.path watches, or None when that unit isn't installed and
    active. Read-only and unprivileged."""
    try:
        out = subprocess.run(
            ["systemctl", "show", TRIGGER_UNIT, "-p", "ActiveState", "-p", "Paths"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    props = dict(line.split("=", 1) for line in (out or "").splitlines() if "=" in line)
    if props.get("ActiveState") != "active":
        return None
    # "Paths=/crw/raven/data/run/restart-fedhub-bridge (PathChanged)"
    path, _, kind = props.get("Paths", "").rpartition(" (")
    if kind != "PathChanged)" or not os.path.isabs(path):
        return None
    return path


def trigger_restart(path: str, requested_by: str | None = None) -> None:
    """Writes the watched file; systemd then restarts the bridge (running, stuck or stopped)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(json.dumps({"action": "restart", "by": requested_by, "at": time.time()}) + "\n")


def run_control_loop(cfg, exit_fn=os._exit):
    """The bridge's side: consume CONTROL_QUEUE forever and exit on "restart".
    Runs in its own thread with its own connection, so it still works when the
    bridge's other threads are stuck, which is when an admin needs it."""
    while True:
        try:
            conn = _connection(cfg)
            channel = conn.channel()
            declare(channel)
            for method, _properties, body in channel.consume(CONTROL_QUEUE):
                # Ack first: an unacked restart would be redelivered to the new
                # bridge and restart it again, forever.
                channel.basic_ack(method.delivery_tag)
                try:
                    message = json.loads(body)
                except ValueError:
                    continue
                if message.get("action") == "restart":
                    log.warning(
                        "Federation Hub bridge: restart requested from the web UI by %s",
                        message.get("by") or "an administrator",
                    )
                    try:
                        conn.close()
                    except Exception:
                        pass
                    exit_fn(RESTART_EXIT_CODE)
                    return
        except Exception:
            log.error("Federation Hub bridge control loop: %s", traceback.format_exc())
            time.sleep(5)

"""The Video Streams list follows MediaMTX, not a stale ready flag.

A notready webhook lost while Raven restarted left a camera showing Live after it had gone
offline. GET /api/video_streams now corrects ready (and the browser copy path) from
MediaMTX's own path list, and leaves things alone when MediaMTX can't be reached.
"""

import json

import pytest

from raven.extensions import db


class _Resp:
    def __init__(self, items):
        self._items = items

    def raise_for_status(self):
        pass

    def json(self):
        return {"items": self._items}


def _stream(path, ready, web_path=None):
    from raven.models.VideoStream import VideoStream

    s = VideoStream()
    s.path = path
    s.protocol = "rtsp"
    s.port = 8554
    s.network_timeout = 10000
    s.ready = ready
    s.web_path = web_path
    s.mediamtx_settings = json.dumps({})
    return s


@pytest.fixture
def streams(app):
    app.config["RAVEN_MEDIAMTX_ENABLE"] = True
    app.config["RAVEN_MEDIAMTX_API_ADDRESS"] = "http://mediamtx.invalid"
    with app.app_context():
        db.session.add(_stream("live/GONE", True, "live/GONE_raven_web"))
        db.session.add(_stream("live/BACK", False))
        db.session.commit()
    return app


def _ready(app):
    from raven.models.VideoStream import VideoStream

    with app.app_context():
        return {s.path: (s.ready, s.web_path) for s in db.session.query(VideoStream).all()}


def test_ready_follows_mediamtx(streams, monkeypatch):
    from raven.blueprints.raven_api import video_api

    monkeypatch.setattr(
        video_api.requests,
        "get",
        lambda *a, **k: _Resp([{"name": "live/BACK", "ready": True}, {"name": "live/GONE", "ready": False}]),
    )
    with streams.app_context():
        video_api.sync_ready_with_mediamtx()
    assert _ready(streams) == {"live/GONE": (False, None), "live/BACK": (True, None)}


def test_nothing_changes_when_mediamtx_is_unreachable(streams, monkeypatch):
    from raven.blueprints.raven_api import video_api

    def boom(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(video_api.requests, "get", boom)
    with streams.app_context():
        video_api.sync_ready_with_mediamtx()
    assert _ready(streams) == {"live/GONE": (True, "live/GONE_raven_web"), "live/BACK": (False, None)}

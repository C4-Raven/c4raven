"""The bridge reconnects by itself when its hub no longer lists it (e.g. after the hub restarted)."""

import queue

import pytest

from raven.controllers import federation_hub_daemon as fhd
from raven.controllers.federation_hub_daemon import FigFederateClient


class Response:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(self.status)

    def json(self):
        return self.body


def hub(connections=None, down=False):
    def request(method, path, **kwargs):
        assert (method, path) == ("GET", "/getActiveConnections")
        if down:
            raise ConnectionError("hub down")
        return Response(connections)
    return request


OURS = {"connectionId": "raven-federate-414976d9-136224485575208", "remoteServerId": ""}
PARTNER = {"connectionId": "pub_d87dd61f-622713fa-a6fJqyNC", "remoteServerId": "622713fa-a6fJqyNC"}


class FakeChannel:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def bridge(monkeypatch):
    import threading

    client = object.__new__(FigFederateClient)
    client.self_uid = "raven-federate"
    client._cfg = {}
    client._channel_lock = threading.Lock()
    client._channel = FakeChannel()
    client._stub = None
    client._stream_generation = 0
    client._outbound_queue = queue.Queue()
    monkeypatch.setattr(client, "_build_grpc_channel", lambda cfg: FakeChannel())
    monkeypatch.setattr(fhd.fig_pb2_grpc, "FederatedChannelStub", lambda channel: ("stub", channel))
    return client


def test_listed_bridge_is_left_alone(bridge):
    first = bridge._channel
    assert bridge._watchdog_tick(hub([OURS, PARTNER]), misses=1) == 0
    assert bridge._channel is first and not first.closed


def test_reconnects_after_repeated_misses(bridge):
    first = bridge._channel
    misses = bridge._watchdog_tick(hub([PARTNER]), misses=0)
    assert misses == 1 and not first.closed  # one miss could be a moment mid-reconnect
    misses = bridge._watchdog_tick(hub([PARTNER]), misses=misses)
    assert misses == 0
    assert first.closed  # cancels both streams, which reopen on the new channel
    assert bridge._channel is not first and bridge._stub == ("stub", bridge._channel)


def test_hub_api_down_is_not_a_miss(bridge):
    first = bridge._channel
    assert bridge._watchdog_tick(hub(down=True), misses=1) == 1
    assert not first.closed


def test_same_prefix_from_another_uid_does_not_count(bridge):
    other = {"connectionId": "raven-federate2-abc-1"}
    assert bridge._registered_with_hub(hub([other])) is False
    assert bridge._registered_with_hub(hub([OURS])) is True


def test_stale_generator_hands_its_event_back(bridge):
    old = bridge._server_event_generator(generation=1)
    bridge._stream_generation = 1
    bridge._outbound_queue.put("e1")
    assert next(old) == "e1"

    # A new call starts; the old generator's thread is still waiting in get()
    bridge._stream_generation = 2
    bridge._outbound_queue.put("e2")
    with pytest.raises(StopIteration):
        next(old)
    # e2 wasn't lost: the current call's generator gets it
    new = bridge._server_event_generator(generation=2)
    assert next(new) == "e2"


def test_watchdog_settings():
    assert fhd.WATCHDOG_INTERVAL_SECONDS >= 30
    assert fhd.WATCHDOG_MISSES_BEFORE_RECONNECT >= 2

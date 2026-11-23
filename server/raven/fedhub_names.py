"""
Friendly names for federated devices' ``federated_from``.

A federated EUD's ``federated_from`` is a Federation Hub provenance id: the "full id" of
a hub (or TAK Server) the event passed through, ``"<server id>-<8 random chars>"`` (live:
``"622713fa68fa49d8a240dc3fe9d65545-ODmwo4kG"``). A hub stamps its own full id on
everything it forwards (FederationHubBrokerService builds its ``FederateProvenance`` from
``FederationHubServerConfig.getFullId()``; FedhubGuardedStreamHolder adds it to the
event's provenance set), and a remote hub or server presents the same full id as
``Subscription.identity.serverId`` when it connects, which the hub reports as that
connection's ``remoteServerId`` in ``getActiveConnections``.

So a provenance id names a partner when it equals the ``remoteServerId`` of one of the
partner's live connections (as attributed on the Federation Hub page by
:func:`raven.fedhub_partners.build_partners`). Our own hub's full id is in every event's
provenance too; it starts with ``getBrokerConfig().id`` and is never a partner.

Hub lookups are cached per process and never raise: the EUD list is polled, and a slow or
down hub must not slow it down or break it. A name learnt once is remembered for the life
of the process, so a device from a partner that has just disconnected keeps its name.
"""

import threading
import time

import requests

from raven import fedhub_partners as fp
from raven.fedhub_settings import HubNotConfigured, hub_api_configured

# How long a successful / failed hub lookup is reused
TTL_SECONDS = 60
FAILURE_TTL_SECONDS = 30
# Per hub request; the hub API normally answers in milliseconds
REQUEST_TIMEOUT_SECONDS = 3
_MAX_REMEMBERED = 1000

_lock = threading.Lock()
_state = {"at": None, "ok": False, "own_hub_id": None, "names": {}}


def reset():
    """Forget everything (tests)."""
    with _lock:
        _state.update(at=None, ok=False, own_hub_id=None, names={})


def server_base(provenance_id):
    """The stable part of a full id: the hub's configured id, without the random suffix
    (which a hub picks afresh when it restarts)."""
    head, sep, tail = (provenance_id or "").rpartition("-")
    return head if sep and head else (provenance_id or "")


def is_own_hub(provenance_id, own_hub_id):
    return bool(own_hub_id and provenance_id) and server_base(provenance_id) == own_hub_id


def pick_provenance(ids, own_hub_id):
    """The provenance id that best says where an event came from: the first one that isn't
    our own hub (the hub keeps provenance in a set, so the order means nothing), else the
    first one."""
    ids = [i for i in ids or [] if i]
    for provenance_id in ids:
        if not is_own_hub(provenance_id, own_hub_id):
            return provenance_id
    return ids[0] if ids else None


def _get(request, path, missing_ok=False):
    r = request("GET", path)
    if missing_ok and r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json() if r.content else None


def names_from_hub(request):
    """``(own hub id, {remoteServerId: partner name})`` from the hub's live state, read
    through ``request(method, path)`` (see :func:`request_fn`). Plain GETs rather than
    :class:`raven.fedhub_partners.HubClient`, whose messages need Flask-Babel, which the
    federation daemon doesn't set up. Raises on any hub or parse failure.
    """
    config = _get(request, "/getBrokerConfig") or {}
    own_hub_id = config.get("id") or None
    known = _get(request, "/getKnownCaGroups") or []
    # 404 is the hub's "no active policy" answer: no partners, but the id still counts
    policy = _get(request, "/getActivePolicy", missing_ok=True) or {}
    connections = _get(request, "/getActiveConnections") or []
    own = fp.own_ca(known)
    partners, _, _ = fp.build_partners(
        policy if isinstance(policy.get("policy"), dict) else {},
        own["uid"] if own else None, known, connections, {}, None,
    )
    names = {}
    for partner in partners:
        for conn in partner.get("connections") or []:
            server_id = conn.get("remoteServerId")
            if server_id and partner.get("name"):
                names[server_id] = partner["name"]
    return own_hub_id, names


def request_fn(config, timeout=REQUEST_TIMEOUT_SECONDS):
    """A ``request(method, path, **kwargs)`` function using Raven's fedhub-admin client cert from a
    Flask config mapping."""
    base = config.get("RAVEN_FEDHUB_API_ADDRESS")
    cert = (config.get("RAVEN_FEDHUB_CLIENT_CERT"), config.get("RAVEN_FEDHUB_CLIENT_KEY"))
    verify = config.get("RAVEN_FEDHUB_CA_BUNDLE")
    if verify is None:
        verify = True

    configured = hub_api_configured(config)

    def request(method, path, **kwargs):
        if not configured:
            raise HubNotConfigured("No Federation Hub is set up on this server")
        return requests.request(method, f"{base}{path}", cert=cert, verify=verify, timeout=timeout, **kwargs)

    return request


def _refresh(request_factory, now):
    try:
        own_hub_id, names = names_from_hub(request_factory())
    except Exception:  # noqa: BLE001 -- any hub/parse failure just means "no names right now"
        _state.update(at=now, ok=False)
        return
    remembered = dict(_state["names"])
    remembered.update(names)
    if len(remembered) > _MAX_REMEMBERED:
        remembered = names
    _state.update(at=now, ok=True, own_hub_id=own_hub_id or _state["own_hub_id"], names=remembered)


def _snapshot(request_factory, now=None):
    now = time.monotonic() if now is None else now
    with _lock:
        at = _state["at"]
        ttl = TTL_SECONDS if _state["ok"] else FAILURE_TTL_SECONDS
        if at is None or now - at >= ttl:
            _refresh(request_factory, now)
        return _state["own_hub_id"], dict(_state["names"])


def _default_request_factory():
    from flask import current_app

    return request_fn(current_app.config)


def _enabled():
    try:
        from flask import current_app

        return hub_api_configured(current_app.config)
    except RuntimeError:
        return False


def lookup(provenance_id, names, own_hub_id=None):
    """The partner name for one provenance id from a ``names`` map, or None."""
    if not provenance_id or is_own_hub(provenance_id, own_hub_id):
        return None
    if provenance_id in names:
        return names[provenance_id]
    base = server_base(provenance_id)
    for server_id, name in names.items():
        if server_base(server_id) == base:
            return name
    return None


def resolver(request_factory=None):
    """A function ``federated_from -> partner name or None`` for one request. The hub is
    only asked (at most once, and only if the cache is stale) when it's first called with
    a non-empty id."""
    loaded = {}

    def resolve(provenance_id):
        if not provenance_id:
            return None
        if not loaded:
            if request_factory is None and not _enabled():
                loaded["v"] = (None, {})
            else:
                loaded["v"] = _snapshot(request_factory or _default_request_factory)
        own_hub_id, names = loaded["v"]
        return lookup(provenance_id, names, own_hub_id)

    return resolve


def own_hub_id(request_factory=None):
    """This hub's configured id (``getBrokerConfig().id``), or None when unknown."""
    if request_factory is None and not _enabled():
        return None
    return _snapshot(request_factory or _default_request_factory)[0]

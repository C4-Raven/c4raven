"""
Meshtastic channels: stored from a meshtastic.org share URL or from individual settings, each
with its own share URL, plus the in-memory map of downlink channels (name -> PSK) the Meshtastic
controller decrypts with.

Off unless RAVEN_ENABLE_MESHTASTIC is set: then every route here answers 404. On, reading the
channels is open to administrators and to read-only admins with the "meshtastic" area; creating,
deleting and generating PSKs is for administrators only. Changes are logged by the central admin
action audit hook (raven/audit.py).
"""

import base64
import functools
import secrets

import bleach
from flask import Blueprint
from flask import current_app as app
from flask import jsonify, request
from flask_babel import gettext
from flask_security import auth_required, current_user, roles_required
from meshtastic import apponly_pb2, channel_pb2, config_pb2
from sqlalchemy.exc import IntegrityError

from raven.admin_view import admin_view
from raven.blueprints.raven_api.api import paginate, search
from raven.extensions import db, logger
from raven.models.Meshtastic import MeshtasticChannel

meshtastic_api_blueprint = Blueprint("meshtastic_api_blueprint", __name__)

SHARE_URL_PREFIX = "https://meshtastic.org/e/#"


def _error(message, status=400):
    return jsonify({"success": False, "error": message}), status


def meshtastic_enabled() -> bool:
    """RAVEN_ENABLE_MESHTASTIC: the one switch for the Meshtastic page, this API and the MQTT bridge."""
    return bool(app.config.get("RAVEN_ENABLE_MESHTASTIC", False))


def _meshtastic_on(f):
    """404 and nothing else while Meshtastic is turned off on this server."""

    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not meshtastic_enabled():
            return _error(gettext("Meshtastic is turned off on this server"), 404)
        return f(*args, **kwargs)

    return wrapped


def downlink_channels() -> dict:
    """RAVEN_MESHTASTIC_DOWNLINK_CHANNELS as a dict (the default config value is a list)."""
    channels = app.config.get("RAVEN_MESHTASTIC_DOWNLINK_CHANNELS")
    if not isinstance(channels, dict):
        channels = {}
        app.config["RAVEN_MESHTASTIC_DOWNLINK_CHANNELS"] = channels
    return channels


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def _channel_set_from_url(url: str):
    encoded = bleach.clean(url.split("#")[-1]) + "=="
    channel_set = apponly_pb2.ChannelSet()
    channel_set.ParseFromString(base64.urlsafe_b64decode(encoded))
    return channel_set


def _channel_set_from_settings(body: dict):
    def value(key):
        # Falsy enum/int/string values are left unset
        return body.get(key) or None

    psk = body.get("psk")
    lora_config = config_pb2.Config.LoRaConfig(
        use_preset=True,
        modem_preset=value("modem_preset"),
        region=value("lora_region"),
        hop_limit=value("lora_hop_limit"),
        tx_enabled=body.get("lora_tx_enabled"),
        tx_power=value("lora_tx_power"),
        sx126x_rx_boosted_gain=body.get("lora_sx126x_rx_boosted_gain"),
    )
    settings = channel_pb2.ChannelSettings(
        psk=base64.urlsafe_b64decode(psk) if psk else b"",
        name=value("name"),
        uplink_enabled=body.get("uplink_enabled"),
        downlink_enabled=body.get("downlink_enabled"),
        module_settings=channel_pb2.ModuleSettings(position_precision=value("position_precision")),
    )
    return apponly_pb2.ChannelSet(settings=[settings], lora_config=lora_config)


@meshtastic_api_blueprint.route("/api/meshtastic/channel", methods=["POST"])
@auth_required()
@_meshtastic_on
@roles_required("administrator")
def create_channel():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}

    try:
        if body.get("url"):
            channel_set = _channel_set_from_url(str(body["url"]))
        else:
            channel_set = _channel_set_from_settings(body)
    except Exception as e:
        return _error(str(e))

    if not channel_set.settings:
        return _error(gettext("The channel set has no channels"))

    lora = channel_set.lora_config
    url = None
    try:
        for channel in channel_set.settings:
            single = apponly_pb2.ChannelSet()
            single.lora_config.CopyFrom(lora)
            single.settings.append(channel)
            url = SHARE_URL_PREFIX + _b64(single.SerializeToString())
            psk = _b64(channel.psk)

            if channel.downlink_enabled:
                channels = downlink_channels()
                if channel.name not in channels:
                    # The same base64 text the controller loads from the database
                    channels[channel.name] = psk

            row = MeshtasticChannel()
            row.psk = psk
            row.name = channel.name
            row.uplink_enabled = channel.uplink_enabled
            row.downlink_enabled = channel.downlink_enabled
            row.position_precision = channel.module_settings.position_precision
            row.lora_region = int(lora.region)
            row.lora_hop_limit = lora.hop_limit
            row.lora_tx_enabled = lora.tx_enabled
            row.lora_tx_power = lora.tx_power
            row.lora_sx126x_rx_boosted_gain = lora.sx126x_rx_boosted_gain
            row.modem_preset = int(lora.modem_preset)
            row.url = url
            try:
                with db.session.begin_nested():
                    db.session.add(row)
            except IntegrityError:
                # Already stored: its URL is still the answer, and later channels are still saved
                logger.info(f"The Meshtastic channel {channel.name} is already stored")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to save Meshtastic channels: {e}")
        return _error(str(e))

    return jsonify({"success": True, "url": url})


@meshtastic_api_blueprint.route("/api/meshtastic/channel")
@auth_required()
@_meshtastic_on
@admin_view("meshtastic")
def get_channel():
    # A channel's key (PSK) is in its psk field and inside its share URL. Read-only admins see
    # the channels but neither, and cannot search by URL (that would test guesses at the key).
    viewer = not current_user.has_role("administrator")
    query = db.session.query(MeshtasticChannel)
    for field in ("id", "name") if viewer else ("id", "name", "url"):
        query = search(query, MeshtasticChannel, field)
    response = paginate(query, MeshtasticChannel)
    if viewer and getattr(response, "is_json", False):
        data = response.get_json()
        for channel in data.get("results", []):
            channel["psk"] = None
            channel["url"] = ""
        response = jsonify(data)
    return response


@meshtastic_api_blueprint.route("/api/meshtastic/channel", methods=["DELETE"])
@auth_required()
@_meshtastic_on
@roles_required("administrator")
def delete_channel():
    if "url" not in request.args:
        return _error(gettext("Please provide the URL of the channel to delete"))
    url = bleach.clean(request.args.get("url"))

    try:
        channel = db.session.query(MeshtasticChannel).filter_by(url=url).one()
        name = channel.name
        db.session.delete(channel)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return _error(str(e))

    downlink_channels().pop(name, None)
    return jsonify({"success": True})


@meshtastic_api_blueprint.route("/api/meshtastic/generate_psk")
@auth_required()
@_meshtastic_on
@roles_required("administrator")
def generate_psk():
    return jsonify({"success": True, "psk": _b64(secrets.token_bytes(32))})

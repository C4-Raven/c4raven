from dataclasses import dataclass

from sqlalchemy.orm import Mapped, mapped_column

from raven.extensions import db
from raven.models.columns import text
from raven.models.serialization import columns

# Names of the Meshtastic protobuf enum codes stored on a channel; an unknown code reads as entry 0
MODEM_PRESETS = {
    0: "LONG_FAST",
    1: "LONG_SLOW",
    2: "VERY_LONG_SLOW",
    3: "MEDIUM_SLOW",
    4: "MEDIUM_FAST",
    5: "SHORT_SLOW",
    6: "SHORT_FAST",
    7: "LONG_MODERATE",
    8: "SHORT_TURBO",
}

LORA_REGIONS = {
    0: "UNSET",
    1: "US",
    2: "EU_433",
    3: "EU_868",
    4: "CN",
    5: "JP",
    6: "ANZ",
    7: "KR",
    8: "TW",
    9: "RU",
    10: "IN",
    11: "NZ_865",
    12: "TH",
    13: "LORA_24",
    14: "UA_433",
    15: "UA_868",
    16: "MY_433",
    17: "MY_919",
    18: "SG_923",
}


# Radio settings reported as stored (to_json() names the two enum codes)
_RADIO = (
    "name", "uplink_enabled", "downlink_enabled", "position_precision", "lora_region",
    "lora_hop_limit", "lora_tx_enabled", "lora_tx_power", "lora_sx126x_rx_boosted_gain",
    "modem_preset", "url",
)  # fmt: skip


def _enum_name(names, code):
    return names.get(code, names[0])


@dataclass
class MeshtasticChannel(db.Model):
    """A Meshtastic channel bridged by the server, keyed by its share URL."""

    __tablename__ = "meshtastic_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    psk: Mapped[str | None] = text()
    name: Mapped[str | None] = text()
    uplink_enabled: Mapped[bool] = mapped_column(default=False)
    downlink_enabled: Mapped[bool] = mapped_column(default=False)
    # Bits of position kept: 0 off, 11 low, 16 medium, 32 full
    position_precision: Mapped[int] = mapped_column(default=32)
    lora_region: Mapped[int] = mapped_column(default=0)
    lora_hop_limit: Mapped[int] = mapped_column(default=3)
    lora_tx_enabled: Mapped[bool] = mapped_column(default=True)
    lora_tx_power: Mapped[int] = mapped_column(default=30)
    lora_sx126x_rx_boosted_gain: Mapped[bool] = mapped_column(default=True)
    modem_preset: Mapped[int] = mapped_column(default=0)
    url: Mapped[str] = text(nullable=False, unique=True)

    def serialize(self):
        channel = {"psk": self.psk or None}
        channel.update(columns(self, _RADIO))
        return channel

    def to_json(self):
        channel = self.serialize()
        channel["lora_region"] = _enum_name(LORA_REGIONS, self.lora_region)
        channel["modem_preset"] = _enum_name(MODEM_PRESETS, self.modem_preset)
        return channel

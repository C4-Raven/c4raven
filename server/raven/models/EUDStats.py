from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from raven.extensions import db
from raven.functions import bytes_to_gigabytes, bytes_to_megabytes
from raven.models.columns import owned_by, text
from raven.models.serialization import columns, iso

# Byte counters and the unit to_json() reports them in; everything else is passed through
_IN_MEGABYTES = (
    "heap_free_size",
    "heap_current_size",
    "heap_max_size",
    "deviceDataRx",
    "deviceDataTx",
)
_IN_GIGABYTES = ("storage_total", "storage_available")


@dataclass
class EUDStats(db.Model):
    """One health report from a device: battery, memory, storage and data counters."""

    __tablename__ = "eud_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime | None] = mapped_column(index=True)
    eud_uid: Mapped[str] = text(owned_by("euds.uid"), nullable=False)
    heap_free_size: Mapped[int | None] = mapped_column(BigInteger)
    app_framerate: Mapped[int | None] = mapped_column()
    storage_total: Mapped[int | None] = mapped_column(BigInteger)
    heap_current_size: Mapped[int | None] = mapped_column()
    battery: Mapped[int | None] = mapped_column()
    battery_temp: Mapped[int | None] = mapped_column()
    deviceDataRx: Mapped[int | None] = mapped_column(BigInteger)
    heap_max_size: Mapped[int | None] = mapped_column(BigInteger)
    storage_available: Mapped[int | None] = mapped_column(BigInteger)
    deviceDataTx: Mapped[int | None] = mapped_column(BigInteger)
    ip_address: Mapped[str | None] = text()
    battery_status: Mapped[str | None] = text()

    eud = relationship("EUD", back_populates="stats", uselist=False)

    # battery_temp is stored but not reported
    _REPORTED = (
        "timestamp", "eud_uid", "heap_free_size", "app_framerate", "storage_total",
        "heap_current_size", "battery", "deviceDataRx", "heap_max_size", "storage_available",
        "deviceDataTx", "ip_address", "battery_status",
    )  # fmt: skip

    def serialize(self):
        return columns(self, self._REPORTED)

    def to_json(self):
        report = self.serialize()
        report["timestamp"] = iso(self.timestamp)
        for name in _IN_MEGABYTES:
            report[name] = bytes_to_megabytes(report[name])
        for name in _IN_GIGABYTES:
            report[name] = bytes_to_gigabytes(report[name])
        return report

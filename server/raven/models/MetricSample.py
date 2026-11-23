from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from raven.extensions import db
from raven.functions import iso8601_string_from_datetime


@dataclass
class MetricSample(db.Model):
    """Once-a-minute snapshot of the metric counter totals (naive UTC timestamp). The Dashboard
    rate chart uses the difference between consecutive rows."""

    __tablename__ = "metric_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    cot_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cot_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    bytes_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    euds_connected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def to_json(self):
        return {
            "timestamp": iso8601_string_from_datetime(self.timestamp) if self.timestamp else None,
            "cot_received": self.cot_received,
            "cot_sent": self.cot_sent,
            "bytes_received": self.bytes_received,
            "bytes_sent": self.bytes_sent,
            "euds_connected": self.euds_connected,
        }

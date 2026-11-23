from dataclasses import dataclass

from sqlalchemy import LargeBinary
from sqlalchemy.orm import Mapped, mapped_column

from raven.extensions import db
from raven.models.columns import text


@dataclass
class APSchedulerJobs(db.Model):
    """APScheduler's own job table (SQLAlchemyJobStore), mapped so it can be read and migrated.
    APScheduler creates and writes it, so these columns must stay exactly as its job store
    defines them."""

    __tablename__ = "apscheduler_jobs"

    id: Mapped[str] = text(primary_key=True)
    next_run_time: Mapped[float | None] = mapped_column(index=True)
    job_state: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

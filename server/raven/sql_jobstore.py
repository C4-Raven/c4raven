from apscheduler.jobstores.base import ConflictingIdError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore


class SQLJobStore(SQLAlchemyJobStore):
    """SQLAlchemy job store that keeps an already stored job when it is added again.

    Jobs from config are added at every start without replace_existing, so a stored job (whose
    schedule an administrator may have changed) wins over the configured one."""

    def add_job(self, job):
        try:
            super().add_job(job)
        except ConflictingIdError:
            pass

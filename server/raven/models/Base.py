from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base shared by every model (passed to SQLAlchemy() as model_class)."""

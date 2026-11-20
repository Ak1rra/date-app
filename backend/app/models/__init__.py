"""ORM models. Import every model module here so Alembic autogenerate sees them."""

from app.models.base import Base

__all__ = ["Base"]

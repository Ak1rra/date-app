"""The couple space: couples and their (at most two) active members."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk


class Couple(UUIDPk, CreatedAt, Base):
    __tablename__ = "couples"
    __table_args__ = (CheckConstraint("status IN ('active', 'archived')", name="status_valid"),)

    name: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(64), server_default=text("'UTC'"))
    # NFR-PRIV-1: only a label and coarse coordinates are stored, never the raw address text.
    # Numeric(5, 2): Postgres rounds to 2 dp on insert, so whatever a caller passes, only a
    # coarse position is ever stored.
    home_area_label: Mapped[str | None] = mapped_column(String(200))
    home_lat: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    home_lng: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    home_place_id: Mapped[str | None] = mapped_column(String(200))
    home_geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoupleMember(UUIDPk, CreatedAt, Base):
    __tablename__ = "couple_members"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'partner')", name="role_valid"),
        # A user has at most one active couple; a couple has at most one active member per
        # role (so at most two members).
        Index(
            "uq_couple_members_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
        Index(
            "uq_couple_members_active_role",
            "couple_id",
            "role",
            unique=True,
            postgresql_where=text("left_at IS NULL"),
        ),
    )

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(16))
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

"""After-date feedback: each partner's rating, the planner's read, and the taste summary
derived from them (FR-RATE-*, AI-6)."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk

_EMPTY_OBJECT = text("'{}'::jsonb")


class Rating(UUIDPk, CreatedAt, Base):
    """One partner's rating of a completed itinerary. `stop_thumbs` maps stop id to "up" or
    "down"."""

    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("itinerary_id", "user_id"),
        CheckConstraint("stars BETWEEN 1 AND 5", name="stars_range"),
    )

    itinerary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    stars: Mapped[int] = mapped_column(SmallInteger)
    would_again: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(280))
    stop_thumbs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PlannerRead(UUIDPk, CreatedAt, Base):
    """The planner's own read of how the date went."""

    __tablename__ = "planner_reads"
    __table_args__ = (
        UniqueConstraint("itinerary_id", "planner_user_id"),
        CheckConstraint("enjoyment BETWEEN 1 AND 5", name="enjoyment_range"),
    )

    itinerary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE")
    )
    planner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    enjoyment: Mapped[int] = mapped_column(SmallInteger)
    highlights: Mapped[str | None] = mapped_column(Text)
    misses: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TasteSummary(UUIDPk, CreatedAt, Base):
    """What feedback has taught the planner about one partner profile (AI-6)."""

    __tablename__ = "taste_summaries"

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    about_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_profiles.id", ondelete="CASCADE"), unique=True
    )
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)
    source_counts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

"""Itineraries and what hangs off them: stops, travel legs, checklist, surprise hints
(FR-ITIN-*, FR-SURP-*)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk

_EMPTY_ARRAY = text("'[]'::jsonb")


class Itinerary(UUIDPk, CreatedAt, Base):
    """A planned date. `partner_reaction` is `{kind: heart|move, suggested_start?, note?, at}`."""

    __tablename__ = "itineraries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'scheduled', 'completed', 'cancelled')", name="status_valid"
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at", name="ends_after_start"
        ),
        Index("ix_itineraries_couple_id_starts_at", "couple_id", "starts_at"),
    )

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    idea_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ideas.id", ondelete="SET NULL"))
    plan_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_sessions.id", ondelete="SET NULL")
    )
    planner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'draft'"))
    announced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    invitation_message: Mapped[str | None] = mapped_column(Text)
    is_surprise: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reveal_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    revealed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    budget: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    est_total: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'USD'"))
    weather: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    backup_plan: Mapped[str | None] = mapped_column(Text)
    bring_wear: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    partner_reaction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    seen_by_partner_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # The service layer sets this on update; there is no trigger.
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Stop(UUIDPk, CreatedAt, Base):
    """One stop in an itinerary. The (itinerary, position) uniqueness is deferred to commit so
    stops can be reordered in a single transaction."""

    __tablename__ = "stops"
    __table_args__ = (
        UniqueConstraint("itinerary_id", "position", deferrable=True, initially="DEFERRED"),
        CheckConstraint(
            "reveal_policy IN ('with_itinerary', 'at_stop_time')", name="reveal_policy_valid"
        ),
    )

    itinerary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(200))
    place_id: Mapped[str | None] = mapped_column(String(200))
    venue: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    event: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)
    est_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    booking_url: Mapped[str | None] = mapped_column(Text)
    evergreen: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    reveal_policy: Mapped[str] = mapped_column(String(16), server_default=text("'with_itinerary'"))
    warnings: Mapped[list[Any]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)


class TravelLeg(UUIDPk, CreatedAt, Base):
    """Travel from one stop to the next; at most one leg leaves each stop."""

    __tablename__ = "travel_legs"
    __table_args__ = (
        UniqueConstraint("from_stop_id"),
        CheckConstraint(
            "mode IN ('drive', 'transit', 'walk', 'bicycle', 'rideshare')", name="mode_valid"
        ),
    )

    itinerary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE")
    )
    from_stop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stops.id", ondelete="CASCADE"))
    to_stop_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stops.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(16))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    distance_m: Mapped[int | None] = mapped_column(Integer)


class ChecklistItem(UUIDPk, CreatedAt, Base):
    __tablename__ = "checklist_items"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('reserve', 'buy', 'bring', 'arrange', 'wear', 'other')", name="kind_valid"
        ),
    )

    itinerary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16))
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    link: Mapped[str | None] = mapped_column(Text)
    done_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class SurpriseHint(UUIDPk, CreatedAt, Base):
    """A clue released to the surprised partner at `release_at` (FR-SURP-*)."""

    __tablename__ = "surprise_hints"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('dress_code', 'bring', 'ready_by', 'eat', 'energy', "
            "'indoor_outdoor', 'weather', 'travel', 'custom')",
            name="kind_valid",
        ),
        Index("ix_surprise_hints_release_at", "release_at"),
    )

    itinerary_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("itineraries.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(String(300))
    release_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

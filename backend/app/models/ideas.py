"""Date ideas, partner votes on them, and the invite that pairs a partner (FR-IDEA-*, FR-VOTE-*,
FR-PAIR-1)."""

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
_EMPTY_OBJECT = text("'{}'::jsonb")


class Idea(UUIDPk, CreatedAt, Base):
    """A date idea, private to its creator until shared (FR-IDEA-*). `venue` is a display
    snapshot that is re-fetched per the provider's terms of service, so it is not authoritative."""

    __tablename__ = "ideas"
    __table_args__ = (
        CheckConstraint(
            "source IN ('ai', 'chat', 'manual', 'plan', 'mentioned')", name="source_valid"
        ),
        CheckConstraint("visibility IN ('private', 'shared')", name="visibility_valid"),
        CheckConstraint(
            "best_time IN ('day', 'evening', 'weekend', 'any')", name="best_time_valid"
        ),
        CheckConstraint(
            "indoor_outdoor IN ('indoor', 'outdoor', 'either')", name="indoor_outdoor_valid"
        ),
        CheckConstraint("status IN ('active', 'archived', 'expired')", name="status_valid"),
        Index("ix_ideas_couple_id_visibility_status", "couple_id", "visibility", "status"),
        Index("ix_ideas_created_by_user_id", "created_by_user_id"),
    )

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str] = mapped_column(String(16))
    visibility: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    pitch: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[Any]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    category: Mapped[str | None] = mapped_column(String(32))
    est_cost: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'USD'"))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    best_time: Mapped[str | None] = mapped_column(String(16))
    indoor_outdoor: Mapped[str | None] = mapped_column(String(16))
    grounded: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    place_ids: Mapped[list[Any]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    event_id: Mapped[str | None] = mapped_column(String(200))
    venue: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    valid_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    is_surprise: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Model, plan_session_id, batch id and the like.
    generation: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)
    shared_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # The service layer sets this on update; there is no trigger.
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Vote(UUIDPk, CreatedAt, Base):
    """One partner's vote on a shared idea (FR-VOTE-*)."""

    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("idea_id", "user_id"),
        CheckConstraint("value IN ('yes', 'no', 'maybe')", name="value_valid"),
    )

    idea_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    value: Mapped[str] = mapped_column(String(8))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Invite(UUIDPk, CreatedAt, Base):
    """An invitation to join the couple, stored by token hash and short code (FR-PAIR-1)."""

    __tablename__ = "invites"

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    short_code: Mapped[str] = mapped_column(String(12), unique=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    used_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ProfileSuggestion(UUIDPk, CreatedAt, Base):
    """The owner's suggested changes to a joined partner's profile, which that partner accepts or
    declines (FR-PROF-7). `changes` holds the partial profile fields. There is no `couple_id`:
    the profile already belongs to one couple."""

    __tablename__ = "profile_suggestions"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'accepted', 'declined')", name="status_valid"),
        Index("ix_profile_suggestions_profile_id_status", "profile_id", "status"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_profiles.id", ondelete="CASCADE")
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

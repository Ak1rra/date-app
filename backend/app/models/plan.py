"""Plan mode: a planner's session building options for their partner, and saved templates
(FR-PLAN-*)."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk

_EMPTY_ARRAY = text("'[]'::jsonb")
_EMPTY_OBJECT = text("'{}'::jsonb")


class PlanSession(UUIDPk, CreatedAt, Base):
    """One run of plan mode. `itinerary_id` and `itineraries.plan_session_id` point at each
    other, so this foreign key is created after both tables exist (`use_alter`)."""

    __tablename__ = "plan_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'done', 'archived')", name="status_valid"),
        Index("ix_plan_sessions_planner_user_id_created_at", "planner_user_id", "created_at"),
    )

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    planner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recipient_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_profiles.id", ondelete="CASCADE")
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)
    options: Mapped[list[Any]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    chosen: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    itinerary_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "itineraries.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_plan_sessions_itinerary_id_itineraries",
        )
    )
    status: Mapped[str] = mapped_column(String(16), server_default=text("'open'"))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PlanTemplate(UUIDPk, CreatedAt, Base):
    """A planner's reusable itinerary skeleton (FR-PLAN-*)."""

    __tablename__ = "plan_templates"

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    planner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    # Title, stops without times, budget and notes.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source_itinerary_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("itineraries.id", ondelete="SET NULL")
    )

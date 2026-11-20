"""Planner notes: private to their author (FR-PROF-8)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk


class PlannerNote(UUIDPk, CreatedAt, Base):
    __tablename__ = "planner_notes"
    __table_args__ = (CheckConstraint("kind IN ('note', 'mentioned_idea')", name="kind_valid"),)

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    author_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    # A profile rather than a user, because the partner may not have signed up yet.
    about_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_profiles.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))
    # The attribute named `text` shadows sqlalchemy.text inside this class body, hence the alias.
    text: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

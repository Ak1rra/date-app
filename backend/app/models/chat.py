"""The AI chat: private per-user threads and one shared couple thread (AI-CHAT)."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk

_EMPTY_ARRAY = text("'[]'::jsonb")


class ChatThread(UUIDPk, CreatedAt, Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        CheckConstraint("scope IN ('private', 'shared')", name="scope_valid"),
        CheckConstraint(
            "(scope = 'private') = (owner_user_id IS NOT NULL)", name="owner_matches_scope"
        ),
        # One private thread per user per couple, and one shared thread per couple.
        Index(
            "uq_chat_threads_private_owner",
            "couple_id",
            "owner_user_id",
            unique=True,
            postgresql_where=text("scope = 'private'"),
        ),
        Index(
            "uq_chat_threads_shared",
            "couple_id",
            unique=True,
            postgresql_where=text("scope = 'shared'"),
        ),
    )

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(16))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str | None] = mapped_column(String(120))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class ChatMessage(UUIDPk, CreatedAt, Base):
    """`actions` holds `{id, type, payload, applied_at}` entries. `hidden_for_user_id` hides
    surprise-related messages from the surprised partner."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
        Index("ix_chat_messages_thread_id_created_at", "thread_id", "created_at"),
    )

    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    actions: Mapped[list[Any]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    tool_trace: Mapped[list[Any]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    hidden_for_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

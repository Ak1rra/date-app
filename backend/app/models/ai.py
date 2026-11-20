"""Provider response cache and per-run AI usage accounting (COST-*, INT-*)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk


class ProviderCache(CreatedAt, Base):
    """A cached external-provider response. The key is the sha256 hex of the normalized request,
    so the primary key is (provider, key) and there is no id column."""

    __tablename__ = "provider_cache"
    __table_args__ = (Index("ix_provider_cache_expires_at", "expires_at"),)

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class AIUsage(UUIDPk, CreatedAt, Base):
    """One row per user-facing AI run, with tokens summed across that run's tool loop. The COST-2
    caps count rows by `feature`."""

    __tablename__ = "ai_usage"
    __table_args__ = (
        Index("ix_ai_usage_couple_id_created_at", "couple_id", "created_at"),
        Index("ix_ai_usage_created_at", "created_at"),
    )

    couple_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("couples.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    feature: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer)
    tool_calls: Mapped[int] = mapped_column(Integer)
    est_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    ok: Mapped[bool] = mapped_column(Boolean)

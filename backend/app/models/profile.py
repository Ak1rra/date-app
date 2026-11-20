"""What the planner knows about each partner, the couple's constraints and key dates."""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UUIDPk

_EMPTY_ARRAY = text("'[]'::jsonb")


class PartnerProfile(UUIDPk, CreatedAt, Base):
    """One per slot. `subject_user_id` is null until that partner has signed up (FR-PROF-7),
    which is why availability and notes hang off the profile rather than the user."""

    __tablename__ = "partner_profiles"
    __table_args__ = (
        UniqueConstraint("couple_id", "slot"),
        CheckConstraint("slot IN ('owner', 'partner')", name="slot_valid"),
        CheckConstraint("energy BETWEEN 1 AND 5", name="energy_range"),
        CheckConstraint("social_setting BETWEEN 1 AND 5", name="social_setting_range"),
        CheckConstraint(
            "indoor_outdoor IN ('indoor', 'outdoor', 'either')", name="indoor_outdoor_valid"
        ),
    )

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    slot: Mapped[str] = mapped_column(String(16))
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    display_name: Mapped[str | None] = mapped_column(String(120))
    liked_categories: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    disliked_categories: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    cuisines_liked: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    cuisines_disliked: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    energy: Mapped[int | None] = mapped_column(SmallInteger)
    indoor_outdoor: Mapped[str | None] = mapped_column(String(16))
    social_setting: Mapped[int | None] = mapped_column(SmallInteger)
    loves: Mapped[str | None] = mapped_column(Text)
    hard_nos: Mapped[str | None] = mapped_column(Text)
    dietary: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    allergies: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    accessibility: Mapped[str | None] = mapped_column(Text)
    edited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    # The service layer sets this on update; there is no trigger.
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    confirmed_by_subject_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class AvailabilityWindow(UUIDPk, CreatedAt, Base):
    """A weekly recurring free window (Monday = 0)."""

    __tablename__ = "availability_windows"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("end_time > start_time", name="end_after_start"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_profiles.id", ondelete="CASCADE")
    )
    weekday: Mapped[int] = mapped_column(SmallInteger)
    start_time: Mapped[dt.time] = mapped_column(Time)
    end_time: Mapped[dt.time] = mapped_column(Time)


class AvailabilityBlock(UUIDPk, CreatedAt, Base):
    """A specific date this person is unavailable."""

    __tablename__ = "availability_blocks"

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("partner_profiles.id", ondelete="CASCADE")
    )
    date: Mapped[dt.date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)


class CoupleConstraints(CreatedAt, Base):
    """One row per couple, created with the couple. The couple id is the primary key."""

    __tablename__ = "couple_constraints"
    __table_args__ = (CheckConstraint("alcohol IN ('yes', 'some', 'none')", name="alcohol_valid"),)

    couple_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("couples.id", ondelete="CASCADE"), primary_key=True
    )
    budget_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    budget_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(CHAR(3), server_default=text("'USD'"))
    max_travel_minutes: Mapped[int | None] = mapped_column(Integer)
    max_radius_km: Mapped[int | None] = mapped_column(Integer)
    transport_modes: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_ARRAY)
    alcohol: Mapped[str | None] = mapped_column(String(8))
    kids_pets_notes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class ImportantDate(UUIDPk, CreatedAt, Base):
    __tablename__ = "important_dates"
    __table_args__ = (
        CheckConstraint("kind IN ('anniversary', 'birthday', 'custom')", name="kind_valid"),
    )

    couple_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("couples.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    date: Mapped[dt.date] = mapped_column(Date)
    recurring: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    kind: Mapped[str] = mapped_column(String(16))
    # Whose birthday, for kind = 'birthday'.
    profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("partner_profiles.id", ondelete="CASCADE")
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))

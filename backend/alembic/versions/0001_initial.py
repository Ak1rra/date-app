"""Initial schema: every M1 table (docs/design/m1-foundation.md, "Data model").

Revision ID: 0001
Revises:
Create Date: 2026-11-19
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constraint and index names follow app.models.base.NAMING_CONVENTION, written out in full so
# the migration does not depend on it.


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _empty_array(name: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'[]'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default=sa.text("'UTC'"), nullable=False),
        sa.Column(
            "notification_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_table(
        "magic_link_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requester_ip", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_magic_link_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_magic_link_tokens_token_hash")),
    )
    op.create_index(
        "ix_magic_link_tokens_email_created_at",
        "magic_link_tokens",
        ["email", "created_at"],
        unique=False,
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )

    op.create_table(
        "couples",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default=sa.text("'UTC'"), nullable=False),
        sa.Column("home_area_label", sa.String(length=200), nullable=True),
        sa.Column("home_lat", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("home_lng", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("home_place_id", sa.String(length=200), nullable=True),
        sa.Column("home_geocoded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('active', 'archived')", name=op.f("ck_couples_status_valid")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_couples")),
    )

    op.create_table(
        "couple_members",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'partner')", name=op.f("ck_couple_members_role_valid")),
        sa.ForeignKeyConstraint(
            ["couple_id"],
            ["couples.id"],
            name=op.f("fk_couple_members_couple_id_couples"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_couple_members_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_couple_members")),
    )
    op.create_index(
        "uq_couple_members_active_user",
        "couple_members",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )
    op.create_index(
        "uq_couple_members_active_role",
        "couple_members",
        ["couple_id", "role"],
        unique=True,
        postgresql_where=sa.text("left_at IS NULL"),
    )

    op.create_table(
        "partner_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.String(length=16), nullable=False),
        sa.Column("subject_user_id", sa.Uuid(), nullable=True),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        _empty_array("liked_categories"),
        _empty_array("disliked_categories"),
        _empty_array("cuisines_liked"),
        _empty_array("cuisines_disliked"),
        sa.Column("energy", sa.SmallInteger(), nullable=True),
        sa.Column("indoor_outdoor", sa.String(length=16), nullable=True),
        sa.Column("social_setting", sa.SmallInteger(), nullable=True),
        sa.Column("loves", sa.Text(), nullable=True),
        sa.Column("hard_nos", sa.Text(), nullable=True),
        _empty_array("dietary"),
        _empty_array("allergies"),
        sa.Column("accessibility", sa.Text(), nullable=True),
        sa.Column("edited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("confirmed_by_subject_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("slot IN ('owner', 'partner')", name=op.f("ck_partner_profiles_slot_valid")),
        sa.CheckConstraint("energy BETWEEN 1 AND 5", name=op.f("ck_partner_profiles_energy_range")),
        sa.CheckConstraint(
            "social_setting BETWEEN 1 AND 5", name=op.f("ck_partner_profiles_social_setting_range")
        ),
        sa.CheckConstraint(
            "indoor_outdoor IN ('indoor', 'outdoor', 'either')",
            name=op.f("ck_partner_profiles_indoor_outdoor_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["couple_id"],
            ["couples.id"],
            name=op.f("fk_partner_profiles_couple_id_couples"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"], ["users.id"], name=op.f("fk_partner_profiles_subject_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["edited_by_user_id"],
            ["users.id"],
            name=op.f("fk_partner_profiles_edited_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_partner_profiles")),
        sa.UniqueConstraint("couple_id", "slot", name=op.f("uq_partner_profiles_couple_id")),
    )

    op.create_table(
        "availability_windows",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6", name=op.f("ck_availability_windows_weekday_range")
        ),
        sa.CheckConstraint(
            "end_time > start_time", name=op.f("ck_availability_windows_end_after_start")
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["partner_profiles.id"],
            name=op.f("fk_availability_windows_profile_id_partner_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_availability_windows")),
    )

    op.create_table(
        "availability_blocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["partner_profiles.id"],
            name=op.f("fk_availability_blocks_profile_id_partner_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_availability_blocks")),
    )

    op.create_table(
        "couple_constraints",
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("budget_min", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("budget_max", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("max_travel_minutes", sa.Integer(), nullable=True),
        sa.Column("max_radius_km", sa.Integer(), nullable=True),
        _empty_array("transport_modes"),
        sa.Column("alcohol", sa.String(length=8), nullable=True),
        sa.Column("kids_pets_notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "alcohol IN ('yes', 'some', 'none')", name=op.f("ck_couple_constraints_alcohol_valid")
        ),
        sa.ForeignKeyConstraint(
            ["couple_id"],
            ["couples.id"],
            name=op.f("fk_couple_constraints_couple_id_couples"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name=op.f("fk_couple_constraints_updated_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("couple_id", name=op.f("pk_couple_constraints")),
    )

    op.create_table(
        "important_dates",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("recurring", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('anniversary', 'birthday', 'custom')",
            name=op.f("ck_important_dates_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["couple_id"],
            ["couples.id"],
            name=op.f("fk_important_dates_couple_id_couples"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["partner_profiles.id"],
            name=op.f("fk_important_dates_profile_id_partner_profiles"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_important_dates_created_by_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_important_dates")),
    )

    op.create_table(
        "planner_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("about_profile_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('note', 'mentioned_idea')", name=op.f("ck_planner_notes_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["couple_id"],
            ["couples.id"],
            name=op.f("fk_planner_notes_couple_id_couples"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"], ["users.id"], name=op.f("fk_planner_notes_author_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["about_profile_id"],
            ["partner_profiles.id"],
            name=op.f("fk_planner_notes_about_profile_id_partner_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planner_notes")),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        _created_at(),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("run_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name=op.f("ck_jobs_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_jobs_dedupe_key")),
    )
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_status_run_at", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("planner_notes")
    op.drop_table("important_dates")
    op.drop_table("couple_constraints")
    op.drop_table("availability_blocks")
    op.drop_table("availability_windows")
    op.drop_table("partner_profiles")
    op.drop_index("uq_couple_members_active_role", table_name="couple_members")
    op.drop_index("uq_couple_members_active_user", table_name="couple_members")
    op.drop_table("couple_members")
    op.drop_table("couples")
    op.drop_table("sessions")
    op.drop_index("ix_magic_link_tokens_email_created_at", table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")
    op.drop_table("users")

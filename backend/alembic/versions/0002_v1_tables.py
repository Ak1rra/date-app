"""Every table Milestones 2-7 need (docs/design/v1-schema.md).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constraint and index names follow app.models.base.NAMING_CONVENTION, written out in full so
# the migration does not depend on it.


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), nullable=False)


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _updated_at() -> sa.Column[Any]:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _ts(name: str, *, nullable: bool = True) -> sa.Column[Any]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _empty_array(name: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'[]'::jsonb"),
        nullable=False,
    )


def _empty_object(name: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'{}'::jsonb"),
        nullable=False,
    )


def _jsonb(name: str, *, nullable: bool = True) -> sa.Column[Any]:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=nullable)


def _currency() -> sa.Column[Any]:
    return sa.Column("currency", sa.CHAR(length=3), server_default=sa.text("'USD'"), nullable=False)


def _flag(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.Boolean(), server_default=sa.text("false"), nullable=False)


def _fk(
    table: str, column: str, target: str, ondelete: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{target}.id"],
        name=op.f(f"fk_{table}_{column}_{target}"),
        ondelete=ondelete,
    )


def upgrade() -> None:
    op.create_table(
        "ideas",
        _id(),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("pitch", sa.Text(), nullable=False),
        _empty_array("tags"),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("est_cost", sa.Numeric(precision=10, scale=2), nullable=True),
        _currency(),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("best_time", sa.String(length=16), nullable=True),
        sa.Column("indoor_outdoor", sa.String(length=16), nullable=True),
        _flag("grounded"),
        _empty_array("place_ids"),
        sa.Column("event_id", sa.String(length=200), nullable=True),
        _jsonb("venue"),
        _ts("valid_until"),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False
        ),
        _flag("is_surprise"),
        _empty_object("generation"),
        _ts("shared_at"),
        _updated_at(),
        sa.CheckConstraint(
            "source IN ('ai', 'chat', 'manual', 'plan', 'mentioned')",
            name=op.f("ck_ideas_source_valid"),
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'shared')", name=op.f("ck_ideas_visibility_valid")
        ),
        sa.CheckConstraint(
            "best_time IN ('day', 'evening', 'weekend', 'any')",
            name=op.f("ck_ideas_best_time_valid"),
        ),
        sa.CheckConstraint(
            "indoor_outdoor IN ('indoor', 'outdoor', 'either')",
            name=op.f("ck_ideas_indoor_outdoor_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'expired')", name=op.f("ck_ideas_status_valid")
        ),
        _fk("ideas", "couple_id", "couples", "CASCADE"),
        _fk("ideas", "created_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ideas")),
    )
    op.create_index(
        "ix_ideas_couple_id_visibility_status",
        "ideas",
        ["couple_id", "visibility", "status"],
        unique=False,
    )
    op.create_index("ix_ideas_created_by_user_id", "ideas", ["created_by_user_id"], unique=False)

    op.create_table(
        "votes",
        _id(),
        _created_at(),
        sa.Column("idea_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.String(length=8), nullable=False),
        _updated_at(),
        sa.CheckConstraint("value IN ('yes', 'no', 'maybe')", name=op.f("ck_votes_value_valid")),
        _fk("votes", "idea_id", "ideas", "CASCADE"),
        _fk("votes", "user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_votes")),
        sa.UniqueConstraint("idea_id", "user_id", name=op.f("uq_votes_idea_id")),
    )

    op.create_table(
        "invites",
        _id(),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("short_code", sa.String(length=12), nullable=False),
        _ts("expires_at", nullable=False),
        _ts("used_at"),
        sa.Column("used_by_user_id", sa.Uuid(), nullable=True),
        _ts("revoked_at"),
        _fk("invites", "couple_id", "couples", "CASCADE"),
        _fk("invites", "created_by_user_id", "users"),
        _fk("invites", "used_by_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invites")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_invites_token_hash")),
        sa.UniqueConstraint("short_code", name=op.f("uq_invites_short_code")),
    )

    op.create_table(
        "profile_suggestions",
        _id(),
        _created_at(),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        _empty_object("changes"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
        ),
        _ts("decided_at"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined')",
            name=op.f("ck_profile_suggestions_status_valid"),
        ),
        _fk("profile_suggestions", "profile_id", "partner_profiles", "CASCADE"),
        _fk("profile_suggestions", "author_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_suggestions")),
    )
    op.create_index(
        "ix_profile_suggestions_profile_id_status",
        "profile_suggestions",
        ["profile_id", "status"],
        unique=False,
    )

    # plan_sessions.itinerary_id and itineraries.plan_session_id point at each other: the
    # plan_sessions side is added below, once itineraries exists.
    op.create_table(
        "plan_sessions",
        _id(),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("planner_user_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_profile_id", sa.Uuid(), nullable=False),
        _empty_object("inputs"),
        _empty_array("options"),
        _jsonb("chosen"),
        sa.Column("itinerary_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'open'"), nullable=False),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'archived')", name=op.f("ck_plan_sessions_status_valid")
        ),
        _fk("plan_sessions", "couple_id", "couples", "CASCADE"),
        _fk("plan_sessions", "planner_user_id", "users"),
        _fk("plan_sessions", "recipient_profile_id", "partner_profiles", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_sessions")),
    )
    op.create_index(
        "ix_plan_sessions_planner_user_id_created_at",
        "plan_sessions",
        ["planner_user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "itineraries",
        _id(),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("idea_id", sa.Uuid(), nullable=True),
        sa.Column("plan_session_id", sa.Uuid(), nullable=True),
        sa.Column("planner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        _ts("starts_at"),
        _ts("ends_at"),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False
        ),
        _ts("announced_at"),
        sa.Column("invitation_message", sa.Text(), nullable=True),
        _flag("is_surprise"),
        _ts("reveal_at"),
        _ts("revealed_at"),
        sa.Column("budget", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("est_total", sa.Numeric(precision=10, scale=2), nullable=True),
        _currency(),
        _jsonb("weather"),
        sa.Column("backup_plan", sa.Text(), nullable=True),
        sa.Column("bring_wear", sa.Text(), nullable=True),
        _empty_array("warnings"),
        _jsonb("partner_reaction"),
        _ts("seen_by_partner_at"),
        _updated_at(),
        sa.CheckConstraint(
            "status IN ('draft', 'scheduled', 'completed', 'cancelled')",
            name=op.f("ck_itineraries_status_valid"),
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at",
            name=op.f("ck_itineraries_ends_after_start"),
        ),
        _fk("itineraries", "couple_id", "couples", "CASCADE"),
        _fk("itineraries", "idea_id", "ideas", "SET NULL"),
        _fk("itineraries", "plan_session_id", "plan_sessions", "SET NULL"),
        _fk("itineraries", "planner_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_itineraries")),
    )
    op.create_index(
        "ix_itineraries_couple_id_starts_at",
        "itineraries",
        ["couple_id", "starts_at"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_plan_sessions_itinerary_id_itineraries"),
        "plan_sessions",
        "itineraries",
        ["itinerary_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "stops",
        _id(),
        _created_at(),
        sa.Column("itinerary_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        _ts("starts_at"),
        _ts("ends_at"),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("place_id", sa.String(length=200), nullable=True),
        _jsonb("venue"),
        _jsonb("event"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("est_cost", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("booking_url", sa.Text(), nullable=True),
        _flag("evergreen"),
        sa.Column(
            "reveal_policy",
            sa.String(length=16),
            server_default=sa.text("'with_itinerary'"),
            nullable=False,
        ),
        _empty_array("warnings"),
        sa.CheckConstraint(
            "reveal_policy IN ('with_itinerary', 'at_stop_time')",
            name=op.f("ck_stops_reveal_policy_valid"),
        ),
        _fk("stops", "itinerary_id", "itineraries", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stops")),
        sa.UniqueConstraint(
            "itinerary_id",
            "position",
            name=op.f("uq_stops_itinerary_id"),
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    op.create_table(
        "travel_legs",
        _id(),
        _created_at(),
        sa.Column("itinerary_id", sa.Uuid(), nullable=False),
        sa.Column("from_stop_id", sa.Uuid(), nullable=False),
        sa.Column("to_stop_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "mode IN ('drive', 'transit', 'walk', 'bicycle', 'rideshare')",
            name=op.f("ck_travel_legs_mode_valid"),
        ),
        _fk("travel_legs", "itinerary_id", "itineraries", "CASCADE"),
        _fk("travel_legs", "from_stop_id", "stops", "CASCADE"),
        _fk("travel_legs", "to_stop_id", "stops", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_travel_legs")),
        sa.UniqueConstraint("from_stop_id", name=op.f("uq_travel_legs_from_stop_id")),
    )

    op.create_table(
        "checklist_items",
        _id(),
        _created_at(),
        sa.Column("itinerary_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        _ts("due_at"),
        sa.Column("link", sa.Text(), nullable=True),
        _ts("done_at"),
        _ts("reminder_sent_at"),
        sa.CheckConstraint(
            "kind IN ('reserve', 'buy', 'bring', 'arrange', 'wear', 'other')",
            name=op.f("ck_checklist_items_kind_valid"),
        ),
        _fk("checklist_items", "itinerary_id", "itineraries", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checklist_items")),
    )

    op.create_table(
        "plan_templates",
        _id(),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("planner_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        _jsonb("snapshot", nullable=False),
        sa.Column("source_itinerary_id", sa.Uuid(), nullable=True),
        _fk("plan_templates", "couple_id", "couples", "CASCADE"),
        _fk("plan_templates", "planner_user_id", "users"),
        _fk("plan_templates", "source_itinerary_id", "itineraries", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_templates")),
    )

    op.create_table(
        "surprise_hints",
        _id(),
        _created_at(),
        sa.Column("itinerary_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        _ts("release_at", nullable=False),
        _ts("released_at"),
        sa.CheckConstraint(
            "kind IN ('dress_code', 'bring', 'ready_by', 'eat', 'energy', "
            "'indoor_outdoor', 'weather', 'travel', 'custom')",
            name=op.f("ck_surprise_hints_kind_valid"),
        ),
        _fk("surprise_hints", "itinerary_id", "itineraries", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_surprise_hints")),
    )
    op.create_index("ix_surprise_hints_release_at", "surprise_hints", ["release_at"], unique=False)

    op.create_table(
        "ratings",
        _id(),
        _created_at(),
        sa.Column("itinerary_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stars", sa.SmallInteger(), nullable=False),
        sa.Column("would_again", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.String(length=280), nullable=True),
        _empty_object("stop_thumbs"),
        _updated_at(),
        sa.CheckConstraint("stars BETWEEN 1 AND 5", name=op.f("ck_ratings_stars_range")),
        _fk("ratings", "itinerary_id", "itineraries", "CASCADE"),
        _fk("ratings", "user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ratings")),
        sa.UniqueConstraint("itinerary_id", "user_id", name=op.f("uq_ratings_itinerary_id")),
    )

    op.create_table(
        "planner_reads",
        _id(),
        _created_at(),
        sa.Column("itinerary_id", sa.Uuid(), nullable=False),
        sa.Column("planner_user_id", sa.Uuid(), nullable=False),
        sa.Column("enjoyment", sa.SmallInteger(), nullable=False),
        sa.Column("highlights", sa.Text(), nullable=True),
        sa.Column("misses", sa.Text(), nullable=True),
        _updated_at(),
        sa.CheckConstraint(
            "enjoyment BETWEEN 1 AND 5", name=op.f("ck_planner_reads_enjoyment_range")
        ),
        _fk("planner_reads", "itinerary_id", "itineraries", "CASCADE"),
        _fk("planner_reads", "planner_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planner_reads")),
        sa.UniqueConstraint(
            "itinerary_id", "planner_user_id", name=op.f("uq_planner_reads_itinerary_id")
        ),
    )

    op.create_table(
        "taste_summaries",
        _id(),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("about_profile_id", sa.Uuid(), nullable=False),
        _empty_object("summary"),
        _empty_object("source_counts"),
        _updated_at(),
        _fk("taste_summaries", "couple_id", "couples", "CASCADE"),
        _fk("taste_summaries", "about_profile_id", "partner_profiles", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_taste_summaries")),
        sa.UniqueConstraint("about_profile_id", name=op.f("uq_taste_summaries_about_profile_id")),
    )

    op.create_table(
        "chat_threads",
        _id(),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=True),
        _updated_at(),
        sa.CheckConstraint(
            "scope IN ('private', 'shared')", name=op.f("ck_chat_threads_scope_valid")
        ),
        sa.CheckConstraint(
            "(scope = 'private') = (owner_user_id IS NOT NULL)",
            name=op.f("ck_chat_threads_owner_matches_scope"),
        ),
        _fk("chat_threads", "couple_id", "couples", "CASCADE"),
        _fk("chat_threads", "owner_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_threads")),
    )
    op.create_index(
        "uq_chat_threads_private_owner",
        "chat_threads",
        ["couple_id", "owner_user_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'private'"),
    )
    op.create_index(
        "uq_chat_threads_shared",
        "chat_threads",
        ["couple_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'shared'"),
    )

    op.create_table(
        "chat_messages",
        _id(),
        _created_at(),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        _empty_array("actions"),
        _empty_array("tool_trace"),
        sa.Column("hidden_for_user_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name=op.f("ck_chat_messages_role_valid")
        ),
        _fk("chat_messages", "thread_id", "chat_threads", "CASCADE"),
        _fk("chat_messages", "author_user_id", "users"),
        _fk("chat_messages", "hidden_for_user_id", "users"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
    )
    op.create_index(
        "ix_chat_messages_thread_id_created_at",
        "chat_messages",
        ["thread_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "notifications",
        _id(),
        _created_at(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("couple_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        _empty_object("data"),
        sa.Column(
            "deliver_after",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        _ts("pushed_at"),
        _ts("read_at"),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        _fk("notifications", "user_id", "users", "CASCADE"),
        _fk("notifications", "couple_id", "couples", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_notifications_dedupe_key")),
    )
    op.create_index(
        "ix_notifications_user_id_created_at",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "push_subscriptions",
        _id(),
        _created_at(),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        _ts("last_used_at"),
        _fk("push_subscriptions", "user_id", "users", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscriptions")),
        sa.UniqueConstraint("endpoint", name=op.f("uq_push_subscriptions_endpoint")),
    )

    op.create_table(
        "provider_cache",
        _created_at(),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        _jsonb("payload", nullable=False),
        _ts("expires_at", nullable=False),
        sa.PrimaryKeyConstraint("provider", "key", name=op.f("pk_provider_cache")),
    )
    op.create_index("ix_provider_cache_expires_at", "provider_cache", ["expires_at"], unique=False)

    op.create_table(
        "ai_usage",
        _id(),
        _created_at(),
        sa.Column("couple_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("est_cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        _fk("ai_usage", "couple_id", "couples", "SET NULL"),
        _fk("ai_usage", "user_id", "users", "SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_usage")),
    )
    op.create_index(
        "ix_ai_usage_couple_id_created_at", "ai_usage", ["couple_id", "created_at"], unique=False
    )
    op.create_index("ix_ai_usage_created_at", "ai_usage", ["created_at"], unique=False)


def downgrade() -> None:
    # The plan_sessions <-> itineraries cycle: drop this foreign key first.
    op.drop_constraint(
        op.f("fk_plan_sessions_itinerary_id_itineraries"), "plan_sessions", type_="foreignkey"
    )
    op.drop_table("ai_usage")
    op.drop_table("provider_cache")
    op.drop_table("push_subscriptions")
    op.drop_table("notifications")
    op.drop_table("chat_messages")
    op.drop_table("chat_threads")
    op.drop_table("taste_summaries")
    op.drop_table("planner_reads")
    op.drop_table("ratings")
    op.drop_table("surprise_hints")
    op.drop_table("plan_templates")
    op.drop_table("checklist_items")
    op.drop_table("travel_legs")
    op.drop_table("stops")
    op.drop_table("itineraries")
    op.drop_table("plan_sessions")
    op.drop_table("profile_suggestions")
    op.drop_table("invites")
    op.drop_table("votes")
    op.drop_table("ideas")

# Schema for Milestones 2–7 (migration 0002)

Binding design. One Alembic migration, `0002_v1_tables`, creates every table that
Milestones 2–7 need, so later milestones never fight over migrations. Conventions as in
`m1-foundation.md` (UUID v4 PKs generated in Python via `UUIDPk`, `CreatedAt`,
`timestamptz` everywhere, JSONB arrays default `'[]'::jsonb`, objects `'{}'::jsonb`,
check constraints named via the naming convention). "FK c" = `ondelete="CASCADE"`,
"FK n" = `ondelete="SET NULL"`. Every couple-owned table has `couple_id` FK c → couples.

Status/enum columns are `String(n)` + a `CheckConstraint` listing the values (like
`partner_profiles.slot_valid`).

## Ideas, votes, invites (FR-IDEA-*, FR-VOTE-*, FR-PAIR-1)

| Table | Columns | Constraints / indexes |
|---|---|---|
| `ideas` | `couple_id`, `created_by_user_id` FK users, `source` (`ai`,`chat`,`manual`,`plan`,`mentioned`), `visibility` (`private`,`shared`), `title` String(200), `pitch` Text, `tags` jsonb [], `category` String(32) null, `est_cost` Numeric(10,2) null, `currency` CHAR(3) default 'USD', `duration_minutes` int null, `best_time` (`day`,`evening`,`weekend`,`any`) null, `indoor_outdoor` (`indoor`,`outdoor`,`either`) null, `grounded` bool default false, `place_ids` jsonb [], `event_id` String(200) null, `venue` jsonb null (display snapshot, re-fetched per ToS), `valid_until` timestamptz null, `status` (`active`,`archived`,`expired`) default 'active', `is_surprise` bool default false, `generation` jsonb {} (model, plan_session_id, batch id…), `shared_at` timestamptz null, `updated_at` | index (`couple_id`,`visibility`,`status`); index (`created_by_user_id`) |
| `votes` | `idea_id` FK c, `user_id` FK users, `value` (`yes`,`no`,`maybe`), `updated_at` | unique (`idea_id`,`user_id`) |
| `invites` | `couple_id`, `created_by_user_id` FK users, `token_hash` String(64) unique, `short_code` String(12) unique, `expires_at`, `used_at` null, `used_by_user_id` FK users null, `revoked_at` null | |
| `profile_suggestions` | `profile_id` FK c partner_profiles, `author_user_id` FK users, `changes` jsonb {} (partial profile fields), `note` Text null, `status` (`pending`,`accepted`,`declined`) default 'pending', `decided_at` null | index (`profile_id`,`status`) |

## Plan mode, itineraries (FR-PLAN-*, FR-ITIN-*, FR-SURP-*)

| Table | Columns | Constraints / indexes |
|---|---|---|
| `plan_sessions` | `couple_id`, `planner_user_id` FK users, `recipient_profile_id` FK c partner_profiles, `inputs` jsonb {}, `options` jsonb [], `chosen` jsonb null, `itinerary_id` FK n itineraries null (`use_alter=True`), `status` (`open`,`done`,`archived`) default 'open', `updated_at` | index (`planner_user_id`,`created_at`) |
| `itineraries` | `couple_id`, `idea_id` FK n ideas null, `plan_session_id` FK n plan_sessions null, `planner_user_id` FK users, `title` String(200), `starts_at` null, `ends_at` null, `status` (`draft`,`scheduled`,`completed`,`cancelled`) default 'draft', `announced_at` null, `invitation_message` Text null, `is_surprise` bool default false, `reveal_at` null, `revealed_at` null, `budget` Numeric(10,2) null, `est_total` Numeric(10,2) null, `currency` CHAR(3) default 'USD', `weather` jsonb null, `backup_plan` Text null, `bring_wear` Text null, `warnings` jsonb [], `partner_reaction` jsonb null (`{kind: heart|move, suggested_start?, note?, at}`), `seen_by_partner_at` null, `updated_at` | index (`couple_id`,`starts_at`); check `ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at` |
| `stops` | `itinerary_id` FK c, `position` int, `starts_at` null, `ends_at` null, `title` String(200), `place_id` String(200) null, `venue` jsonb null, `event` jsonb null, `description` Text null, `est_cost` Numeric(10,2) null, `notes` Text null, `booking_url` Text null, `evergreen` bool default false, `reveal_policy` (`with_itinerary`,`at_stop_time`) default 'with_itinerary', `warnings` jsonb [] | unique (`itinerary_id`,`position`) DEFERRABLE INITIALLY DEFERRED (reorders) |
| `travel_legs` | `itinerary_id` FK c, `from_stop_id` FK c stops, `to_stop_id` FK c stops, `mode` (`drive`,`transit`,`walk`,`bicycle`,`rideshare`), `duration_minutes` int, `distance_m` int null | unique (`from_stop_id`) |
| `checklist_items` | `itinerary_id` FK c, `position` int, `text` String(300), `kind` (`reserve`,`buy`,`bring`,`arrange`,`wear`,`other`), `due_at` null, `link` Text null, `done_at` null, `reminder_sent_at` null | |
| `plan_templates` | `couple_id`, `planner_user_id` FK users, `name` String(120), `snapshot` jsonb (title, stops w/o times, budget, notes), `source_itinerary_id` FK n itineraries null | |
| `surprise_hints` | `itinerary_id` FK c, `kind` (`dress_code`,`bring`,`ready_by`,`eat`,`energy`,`indoor_outdoor`,`weather`,`travel`,`custom`), `text` String(300), `release_at`, `released_at` null | index (`release_at`) |

## Feedback (FR-RATE-*, AI-6)

| Table | Columns | Constraints |
|---|---|---|
| `ratings` | `itinerary_id` FK c, `user_id` FK users, `stars` smallint 1–5, `would_again` bool, `comment` String(280) null, `stop_thumbs` jsonb {} (`stop_id → "up"|"down"`), `updated_at` | unique (`itinerary_id`,`user_id`); check stars range |
| `planner_reads` | `itinerary_id` FK c, `planner_user_id` FK users, `enjoyment` smallint 1–5, `highlights` Text null, `misses` Text null, `updated_at` | unique (`itinerary_id`,`planner_user_id`) |
| `taste_summaries` | `couple_id`, `about_profile_id` FK c partner_profiles, `summary` jsonb {}, `source_counts` jsonb {}, `updated_at` | unique (`about_profile_id`) |

## Chat (AI-CHAT)

| Table | Columns | Constraints |
|---|---|---|
| `chat_threads` | `couple_id`, `scope` (`private`,`shared`), `owner_user_id` FK users null, `title` String(120) null, `updated_at` | partial unique (`couple_id`,`owner_user_id`) where scope='private'; partial unique (`couple_id`) where scope='shared'; check (scope='private') = (owner_user_id IS NOT NULL) |
| `chat_messages` | `thread_id` FK c, `role` (`user`,`assistant`), `author_user_id` FK users null, `content` Text, `actions` jsonb [] (`{id, type, payload, applied_at}`), `tool_trace` jsonb [], `hidden_for_user_id` FK users null (surprise-related, see M4) | index (`thread_id`,`created_at`) |

## Notifications (§5.9)

| Table | Columns | Constraints |
|---|---|---|
| `notifications` | `user_id` FK c users, `couple_id` null FK c, `kind` String(40), `title` String(200), `body` Text, `data` jsonb {}, `deliver_after` default now(), `pushed_at` null, `read_at` null, `dedupe_key` String(200) null unique | index (`user_id`,`created_at`) |
| `push_subscriptions` | `user_id` FK c users, `endpoint` Text unique, `p256dh` Text, `auth` Text, `user_agent` Text null, `last_used_at` null | |

## AI + providers (COST-*, INT-*)

| Table | Columns | Constraints |
|---|---|---|
| `provider_cache` | composite PK (`provider` String(32), `key` String(64) = sha256 hex of the normalized request), `payload` jsonb, `expires_at`, `created_at` | index (`expires_at`) |
| `ai_usage` | `couple_id` FK n null, `user_id` FK n null, `feature` String(32), `model` String(64), `input_tokens` int, `output_tokens` int, `cache_read_tokens` int, `cache_write_tokens` int, `tool_calls` int, `est_cost_usd` Numeric(12,6), `ok` bool | index (`couple_id`,`created_at`); index (`created_at`) |

`ai_usage` has one row per user-facing AI run (tokens summed across that run's tool
loop); COST-2 caps count rows by `feature`.

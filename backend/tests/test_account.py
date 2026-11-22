"""GET /me/export and DELETE /me (FR-AUTH-3, NFR-PRIV-2), and the user.purge job."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models import (
    AIUsage,
    AvailabilityWindow,
    ChatMessage,
    ChatThread,
    Couple,
    CoupleMember,
    Idea,
    ImportantDate,
    Itinerary,
    Job,
    Notification,
    PartnerProfile,
    PlannerNote,
    PlannerRead,
    PlanSession,
    PlanTemplate,
    ProfileSuggestion,
    PushSubscription,
    Rating,
    TasteSummary,
    User,
    UserSession,
    Vote,
)
from app.services.auth import HUSBAND_EMAIL, WIFE_EMAIL
from app.worker import handlers
from tests.authutil import browser, call, joined_couple
from tests.conftest import make_settings
from tests.test_choose_profile import choose

pytestmark = pytest.mark.db

ME = "/api/v1/me"
EXPORT = "/api/v1/me/export"
ABSENT_FROM_OWNER = "PARTNER-ONLY"


@dataclass
class Seeded:
    shared_idea: uuid.UUID
    itineraries: dict[str, uuid.UUID]  # planner -> itinerary
    profiles: dict[str, uuid.UUID]  # user id (str) -> profile id


async def seed(
    db: AsyncSession, couple_id: uuid.UUID, owner_id: uuid.UUID, partner_id: uuid.UUID
) -> Seeded:
    """The same set of personal rows for both users. The partner's carry ABSENT_FROM_OWNER in
    their text, so a leak into the owner's export is easy to see."""
    await db.rollback()
    shared = Idea(
        couple_id=couple_id,
        created_by_user_id=owner_id,
        source="manual",
        visibility="shared",
        title="Shared picnic",
        pitch="Shared by the owner",
    )
    db.add(shared)
    itineraries = {
        "owner": Itinerary(couple_id=couple_id, planner_user_id=owner_id, title="Owner plan"),
        "partner": Itinerary(couple_id=couple_id, planner_user_id=partner_id, title="Surprise"),
    }
    itineraries["partner"].is_surprise = True
    db.add_all(itineraries.values())
    shared_thread = ChatThread(couple_id=couple_id, scope="shared")
    db.add(shared_thread)
    await db.flush()

    profiles = {
        str(user_id): (
            await db.execute(
                select(PartnerProfile).where(
                    PartnerProfile.couple_id == couple_id, PartnerProfile.subject_user_id == user_id
                )
            )
        ).scalar_one()
        for user_id in (owner_id, partner_id)
    }
    for user_id, tag in ((owner_id, "OWNER"), (partner_id, ABSENT_FROM_OWNER)):
        profile = profiles[str(user_id)]
        other = partner_id if user_id == owner_id else owner_id
        mine = itineraries["owner" if user_id == owner_id else "partner"]
        theirs = itineraries["partner" if user_id == owner_id else "owner"]
        profile.display_name = f"{tag} name"
        profile.liked_categories = ["museums"]
        profile.cuisines_disliked = ["thai"]
        profile.dietary = ["vegan"]
        profile.energy = 4
        profile.indoor_outdoor = "indoor"
        profile.social_setting = 2
        profile.loves = f"{tag} loves"
        profile.hard_nos = f"{tag} hard no"
        profile.accessibility = f"{tag} access"
        profile.confirmed_by_subject_at = func.now()
        db.add(
            AvailabilityWindow(
                profile_id=profile.id,
                weekday=2,
                start_time=dt.time(18),
                end_time=dt.time(22),
            )
        )
        db.add(
            ImportantDate(
                couple_id=couple_id,
                profile_id=profile.id,
                name=f"{tag} birthday",
                date=dt.date(2000, 5, 4),
                kind="birthday",
                created_by_user_id=user_id,
            )
        )
        db.add(TasteSummary(couple_id=couple_id, about_profile_id=profile.id, summary={"t": tag}))
        db.add(Vote(idea_id=shared.id, user_id=user_id, value="yes"))
        db.add(Rating(itinerary_id=theirs.id, user_id=user_id, stars=4, would_again=True))
        db.add(PlannerRead(itinerary_id=mine.id, planner_user_id=user_id, enjoyment=3))
        db.add(
            PlannerNote(
                couple_id=couple_id,
                author_user_id=user_id,
                about_profile_id=profiles[str(other)].id,
                kind="note",
                text=f"{tag} note",
            )
        )
        db.add(
            PlanSession(
                couple_id=couple_id,
                planner_user_id=user_id,
                recipient_profile_id=profiles[str(other)].id,
            )
        )
        db.add(
            PlanTemplate(
                couple_id=couple_id,
                planner_user_id=user_id,
                name=f"{tag} template",
                snapshot={"title": f"{tag} template", "stops": []},
                source_itinerary_id=mine.id,
            )
        )
        db.add(
            ProfileSuggestion(
                profile_id=profiles[str(other)].id,
                author_user_id=user_id,
                changes={"loves": f"{tag} suggestion"},
            )
        )
        db.add(
            Idea(
                couple_id=couple_id,
                created_by_user_id=user_id,
                source="manual",
                visibility="private",
                title=f"{tag} private idea",
                pitch="p",
                is_surprise=user_id == partner_id,
            )
        )
        db.add(
            Idea(
                couple_id=couple_id,
                created_by_user_id=user_id,
                source="manual",
                visibility="shared",
                title=f"{tag} shared idea",
                pitch="p",
            )
        )
        private_thread = ChatThread(couple_id=couple_id, scope="private", owner_user_id=user_id)
        db.add(private_thread)
        await db.flush()
        db.add(
            ChatMessage(
                thread_id=private_thread.id,
                role="user",
                author_user_id=user_id,
                content=f"{tag} private chat",
            )
        )
        db.add(
            ChatMessage(
                thread_id=shared_thread.id,
                role="user",
                author_user_id=user_id,
                content=f"{tag} shared chat",
            )
        )
        db.add(
            Notification(
                user_id=user_id,
                couple_id=couple_id,
                kind="test",
                title=f"{tag} notification",
                body="b",
            )
        )
        db.add(
            PushSubscription(user_id=user_id, endpoint=f"https://push/{tag}", p256dh="k", auth="a")
        )
    await db.commit()
    return Seeded(
        shared_idea=shared.id,
        itineraries={k: v.id for k, v in itineraries.items()},
        profiles={k: v.id for k, v in profiles.items()},
    )


async def count_for(db: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """How many personal rows of each kind `user_id` still has."""
    await db.rollback()

    async def n(model: type, column: object) -> int:
        stmt = (
            select(func.count())
            .select_from(model)
            .where(  # type: ignore[arg-type]
                column == user_id
            )
        )
        return int((await db.execute(stmt)).scalar_one())

    shared_threads = select(ChatThread.id).where(ChatThread.scope == "shared")
    return {
        "votes": await n(Vote, Vote.user_id),
        "ratings": await n(Rating, Rating.user_id),
        "reads": await n(PlannerRead, PlannerRead.planner_user_id),
        "notes": await n(PlannerNote, PlannerNote.author_user_id),
        "private_ideas": int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Idea)
                    .where(Idea.created_by_user_id == user_id, Idea.visibility == "private")
                )
            ).scalar_one()
        ),
        "shared_ideas": int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Idea)
                    .where(Idea.created_by_user_id == user_id, Idea.visibility == "shared")
                )
            ).scalar_one()
        ),
        "private_threads": await n(ChatThread, ChatThread.owner_user_id),
        "shared_messages": int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ChatMessage)
                    .where(
                        ChatMessage.author_user_id == user_id,
                        ChatMessage.thread_id.in_(shared_threads),
                    )
                )
            ).scalar_one()
        ),
        "notifications": await n(Notification, Notification.user_id),
        "push": await n(PushSubscription, PushSubscription.user_id),
        "plan_sessions": await n(PlanSession, PlanSession.planner_user_id),
        "plan_templates": await n(PlanTemplate, PlanTemplate.planner_user_id),
        "profile_suggestions": await n(ProfileSuggestion, ProfileSuggestion.author_user_id),
    }


PARTNER_KEPT = {
    "votes": 1,
    "ratings": 1,
    "reads": 1,
    "notes": 1,
    "private_ideas": 1,
    "shared_ideas": 1,
    "private_threads": 1,
    "shared_messages": 1,
    "notifications": 1,
    "push": 1,
    "plan_sessions": 1,
    "plan_templates": 1,
    "profile_suggestions": 1,
}
# The owner also made the couple's "Shared picnic"; shared ideas are couple data and stay.
OWNER_KEPT = {**PARTNER_KEPT, "shared_ideas": 2}
OWNER_GONE = {**{k: 0 for k in PARTNER_KEPT}, "shared_ideas": 2}


async def profile_state(db: AsyncSession, profile_id: uuid.UUID) -> PartnerProfile:
    await db.rollback()
    return (
        await db.execute(
            select(PartnerProfile)
            .where(PartnerProfile.id == profile_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def assert_profile_reset(
    db: AsyncSession, profile_id: uuid.UUID, subject: uuid.UUID, *, keep_name: bool = False
) -> None:
    profile = await profile_state(db, profile_id)
    assert profile.subject_user_id == subject
    assert profile.slot in ("owner", "partner")
    assert profile.liked_categories == []
    assert profile.cuisines_disliked == []
    assert profile.dietary == []
    assert profile.allergies == []
    assert profile.energy is None
    assert profile.indoor_outdoor is None
    assert profile.social_setting is None
    assert profile.loves is None
    assert profile.hard_nos is None
    assert profile.accessibility is None
    assert profile.confirmed_by_subject_at is None
    assert (profile.display_name is not None) == keep_name
    for model in (AvailabilityWindow, ImportantDate, TasteSummary):
        column = model.about_profile_id if model is TasteSummary else model.profile_id
        total = (
            await db.execute(select(func.count()).select_from(model).where(column == profile_id))
        ).scalar_one()
        assert total == 0, model


async def assert_profile_kept(db: AsyncSession, profile_id: uuid.UUID) -> None:
    profile = await profile_state(db, profile_id)
    assert profile.display_name is not None
    assert profile.energy == 4
    assert profile.loves is not None
    for model in (AvailabilityWindow, ImportantDate, TasteSummary):
        column = model.about_profile_id if model is TasteSummary else model.profile_id
        total = (
            await db.execute(select(func.count()).select_from(model).where(column == profile_id))
        ).scalar_one()
        assert total == 1, model


# -- export -----------------------------------------------------------------------------------


async def test_export_holds_my_rows_and_none_of_the_partners(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        me = (await owner.get(ME)).json()
        other = (await partner.get(ME)).json()
        seeded = await seed(
            db_session, uuid.UUID(me["couple_id"]), uuid.UUID(me["id"]), uuid.UUID(other["id"])
        )
        response = await owner.get(EXPORT)
        assert response.status_code == 200
        assert response.headers["content-disposition"] == (
            f'attachment; filename="dateapp-export-{dt.datetime.now(dt.UTC).date()}.json"'
        )
        body = response.json()

        assert ABSENT_FROM_OWNER not in response.text
        assert other["id"] not in response.text
        assert other["email"] not in response.text

        assert body["user"]["id"] == me["id"]
        assert body["user"]["email"] == me["email"]
        assert set(body["user"]) >= {"display_name", "timezone", "notification_settings"}
        assert body["exported_at"]
        profile = body["profile"]
        assert profile["profile"]["id"] == str(seeded.profiles[me["id"]])
        assert profile["profile"]["loves"] == "OWNER loves"
        assert [w["weekday"] for w in profile["availability_windows"]] == [2]
        assert [d["name"] for d in profile["important_dates"]] == ["OWNER birthday"]
        assert "taste_summary" not in body
        assert [n["text"] for n in body["planner_notes"]] == ["OWNER note"]
        # My own private and shared ideas, not the partner's (even their surprise).
        assert {i["title"] for i in body["ideas"]} == {
            "Shared picnic",
            "OWNER private idea",
            "OWNER shared idea",
        }
        assert [(v["idea_id"], v["value"]) for v in body["votes"]] == [
            (str(seeded.shared_idea), "yes")
        ]
        assert [r["itinerary_id"] for r in body["ratings"]] == [str(seeded.itineraries["partner"])]
        assert [r["itinerary_id"] for r in body["planner_reads"]] == [
            str(seeded.itineraries["owner"])
        ]
        messages = {m["content"]: m for m in body["chat_messages"]}
        assert set(messages) == {"OWNER private chat", "OWNER shared chat"}
        assert messages["OWNER private chat"]["scope"] == "private"
        assert messages["OWNER shared chat"]["scope"] == "shared"
        assert all(m["thread_id"] and m["created_at"] for m in messages.values())
        assert [n["title"] for n in body["notifications"]] == ["OWNER notification"]

        # The partner's own export is the mirror image.
        theirs = (await partner.get(EXPORT)).json()
        assert [n["text"] for n in theirs["planner_notes"]] == [f"{ABSENT_FROM_OWNER} note"]
        assert "OWNER private" not in str(theirs)


async def test_export_of_a_user_with_no_data_is_empty(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    from tests.authutil import sign_in

    await sign_in(db_app, db_client, "ann@example.com")
    body = (await db_client.get(EXPORT)).json()
    assert body["user"]["email"] == "ann@example.com"
    for key in ("planner_notes", "ideas", "votes", "ratings", "planner_reads", "chat_messages"):
        assert body[key] == []
    assert "taste_summary" not in body


async def test_export_and_delete_need_a_session(db_client: AsyncClient) -> None:
    assert (await db_client.get(EXPORT)).status_code == 401
    assert (await call(db_client, "DELETE", ME)).status_code == 401


# -- delete, NO_LOGIN -------------------------------------------------------------------------


@pytest.fixture
def nl_app(db_schema: str) -> FastAPI:
    return create_app(make_settings(database_url=db_schema, no_login=True))


@pytest.fixture
async def household(nl_app: FastAPI) -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    async with browser(nl_app) as husband, browser(nl_app) as wife:
        assert (await choose(husband, "husband")).status_code == 200
        assert (await choose(wife, "wife")).status_code == 200
        yield husband, wife


async def test_no_login_delete_resets_my_data_and_keeps_the_household(
    nl_app: FastAPI, household: tuple[AsyncClient, AsyncClient], db_session: AsyncSession
) -> None:
    husband, wife = household
    me = (await husband.get(ME)).json()
    other = (await wife.get(ME)).json()
    mine, theirs = uuid.UUID(me["id"]), uuid.UUID(other["id"])
    seeded = await seed(db_session, uuid.UUID(me["couple_id"]), mine, theirs)
    assert await count_for(db_session, mine) == OWNER_KEPT

    response = await call(husband, "DELETE", ME)
    assert response.status_code == 200
    assert response.json() == {"mode": "reset"}

    assert await count_for(db_session, mine) == OWNER_GONE
    await assert_profile_reset(db_session, seeded.profiles[me["id"]], mine, keep_name=True)
    # The partner's data, the couple's shared ideas and itineraries are all intact.
    assert await count_for(db_session, theirs) == PARTNER_KEPT
    await assert_profile_kept(db_session, seeded.profiles[other["id"]])
    itineraries = (await db_session.execute(select(Itinerary.id))).scalars().all()
    assert set(itineraries) == set(seeded.itineraries.values())
    assert await db_session.get(Idea, seeded.shared_idea) is not None

    # The user, membership and session survive; choosing the same profile again still works.
    assert (await husband.get(ME)).status_code == 200
    user = await db_session.get(User, mine, populate_existing=True)
    assert user is not None and user.deleted_at is None and user.email == HUSBAND_EMAIL
    again = await choose(husband, "husband")
    assert again.status_code == 200
    assert again.json()["user"]["id"] == me["id"]
    assert (await choose(wife, "wife")).json()["user"]["email"] == WIFE_EMAIL


# -- delete, email mode -----------------------------------------------------------------------


async def test_email_delete_anonymises_revokes_leaves_and_schedules_the_purge(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        me = (await owner.get(ME)).json()
        other = (await partner.get(ME)).json()
        mine, theirs = uuid.UUID(me["id"]), uuid.UUID(other["id"])
        seeded = await seed(db_session, uuid.UUID(me["couple_id"]), mine, theirs)
        user = await db_session.get(User, mine)
        assert user is not None
        user.timezone = "America/New_York"
        user.notification_settings = {"push": False}
        db_session.add(
            AIUsage(
                couple_id=uuid.UUID(me["couple_id"]),
                user_id=mine,
                feature="test",
                model="test",
                input_tokens=0,
                output_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                tool_calls=0,
                est_cost_usd=0,
                ok=True,
            )
        )
        await db_session.commit()

        response = await call(owner, "DELETE", ME)
        assert response.status_code == 200
        assert response.json() == {"mode": "deleted"}
        assert "session" not in owner.cookies  # the cookie is cleared
        assert (await partner.get(ME)).status_code == 200

        await db_session.rollback()
        user = (
            await db_session.execute(
                select(User).where(User.id == mine).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert user.deleted_at is not None
        assert user.email == f"deleted-{mine}@deleted.invalid"
        assert user.display_name is None
        profile = await profile_state(db_session, seeded.profiles[me["id"]])
        assert profile.display_name is None
        sessions = (
            (await db_session.execute(select(UserSession).where(UserSession.user_id == mine)))
            .scalars()
            .all()
        )
        assert sessions and all(s.revoked_at is not None for s in sessions)
        member = (
            await db_session.execute(select(CoupleMember).where(CoupleMember.user_id == mine))
        ).scalar_one()
        assert member.left_at is not None

        job = (
            await db_session.execute(select(Job).where(Job.dedupe_key == f"user.purge:{mine}"))
        ).scalar_one()
        assert job.kind == "user.purge"
        assert job.payload == {"user_id": str(mine)}
        assert job.status == "queued"
        payload = dict(job.payload)
        delay = job.run_at - dt.datetime.now(dt.UTC)
        assert dt.timedelta(days=29, hours=23) < delay <= dt.timedelta(days=30)

        # Nothing is purged until the job runs.
        assert await count_for(db_session, mine) == OWNER_KEPT

        for _ in range(2):  # the second run is a no-op
            async with db_app.state.sessionmaker() as session:
                await handlers.user_purge(session, payload)
                await session.commit()
            assert await count_for(db_session, mine) == OWNER_GONE
            await assert_profile_reset(db_session, seeded.profiles[me["id"]], mine)
            assert await count_for(db_session, theirs) == PARTNER_KEPT
            await assert_profile_kept(db_session, seeded.profiles[other["id"]])
            user = await db_session.get(User, mine, populate_existing=True)
            assert user is not None
            assert user.timezone == "UTC"
            assert user.notification_settings == {}
            usage = (await db_session.execute(select(AIUsage))).scalar_one()
            assert usage.user_id is None
            assert await db_session.get(Couple, uuid.UUID(me["couple_id"])) is not None
            assert await db_session.get(Idea, seeded.shared_idea) is not None


async def test_purge_deletes_a_solo_couple_and_shared_data(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    from tests.authutil import sign_in

    await sign_in(db_app, db_client, "solo@example.com")
    me = (await db_client.get(ME)).json()
    mine, couple_id = uuid.UUID(me["id"]), uuid.UUID(me["couple_id"])
    shared = Idea(
        couple_id=couple_id,
        created_by_user_id=mine,
        source="manual",
        visibility="shared",
        title="Shared solo idea",
        pitch="A shared idea in an orphaned couple",
    )
    db_session.add(shared)
    await db_session.commit()

    assert (await call(db_client, "DELETE", ME)).status_code == 200
    async with db_app.state.sessionmaker() as session:
        await handlers.user_purge(session, {"user_id": str(mine)})
        await session.commit()
    await db_session.rollback()
    assert await db_session.get(Couple, couple_id, populate_existing=True) is None
    assert await db_session.get(Idea, shared.id, populate_existing=True) is None


async def test_no_login_delete_keeps_partner_and_couple_dates_but_removes_my_date(
    nl_app: FastAPI, household: tuple[AsyncClient, AsyncClient], db_session: AsyncSession
) -> None:
    husband, wife = household
    me = (await husband.get(ME)).json()
    other = (await wife.get(ME)).json()
    couple_id = uuid.UUID(me["couple_id"])
    mine, theirs = uuid.UUID(me["id"]), uuid.UUID(other["id"])
    seeded = await seed(db_session, couple_id, mine, theirs)
    db_session.add_all(
        [
            ImportantDate(
                couple_id=couple_id,
                profile_id=seeded.profiles[me["id"]],
                name="Partner-created on my profile",
                date=dt.date(2001, 1, 1),
                kind="custom",
                created_by_user_id=theirs,
            ),
            ImportantDate(
                couple_id=couple_id,
                profile_id=None,
                name="Anniversary",
                date=dt.date(2001, 2, 2),
                kind="anniversary",
                created_by_user_id=mine,
            ),
        ]
    )
    await db_session.commit()

    response = await call(husband, "DELETE", ME)
    assert response.status_code == 200
    await db_session.rollback()
    dates = (
        (
            await db_session.execute(
                select(ImportantDate).where(ImportantDate.couple_id == couple_id)
            )
        )
        .scalars()
        .all()
    )
    assert {date.name for date in dates} == {
        f"{ABSENT_FROM_OWNER} birthday",
        "Partner-created on my profile",
        "Anniversary",
    }


async def test_purge_cleans_an_older_left_membership(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        me = (await owner.get(ME)).json()
        other = (await partner.get(ME)).json()
        mine, theirs = uuid.UUID(me["id"]), uuid.UUID(other["id"])
        await seed(db_session, uuid.UUID(me["couple_id"]), mine, theirs)

        old_couple = Couple(name="Old couple")
        db_session.add(old_couple)
        await db_session.flush()
        old_profile = PartnerProfile(
            couple_id=old_couple.id,
            slot="owner",
            subject_user_id=mine,
            display_name="Old name",
        )
        db_session.add(old_profile)
        await db_session.flush()
        db_session.add_all(
            [
                CoupleMember(
                    couple_id=old_couple.id,
                    user_id=mine,
                    role="owner",
                    left_at=func.now(),
                ),
                ImportantDate(
                    couple_id=old_couple.id,
                    profile_id=old_profile.id,
                    name="Old birthday",
                    date=dt.date(2002, 3, 3),
                    kind="birthday",
                    created_by_user_id=mine,
                ),
            ]
        )
        await db_session.commit()

        assert (await call(owner, "DELETE", ME)).status_code == 200
        async with db_app.state.sessionmaker() as session:
            await handlers.user_purge(session, {"user_id": str(mine)})
            await session.commit()
        await db_session.rollback()
        assert (
            await db_session.execute(
                select(func.count())
                .select_from(ImportantDate)
                .where(ImportantDate.profile_id == old_profile.id)
            )
        ).scalar_one() == 0
        # The old couple has no active member left, so the purge deletes it and its profile.
        assert await db_session.get(Couple, old_couple.id, populate_existing=True) is None
        assert await db_session.get(PartnerProfile, old_profile.id, populate_existing=True) is None


async def test_purge_ignores_a_user_who_is_not_deleted_or_a_bad_payload(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        me = (await owner.get(ME)).json()
        other = (await partner.get(ME)).json()
        mine = uuid.UUID(me["id"])
        await seed(db_session, uuid.UUID(me["couple_id"]), mine, uuid.UUID(other["id"]))
        async with db_app.state.sessionmaker() as session:
            await handlers.user_purge(session, {"user_id": str(mine)})
            await handlers.user_purge(session, {"user_id": "nope"})
            await handlers.user_purge(session, {"user_id": str(uuid.uuid4())})
            await handlers.user_purge(session, {})
            await session.commit()
        assert await count_for(db_session, mine) == OWNER_KEPT

"""The model's couple context: what it holds and, above all, what it must never hold."""

import datetime as dt
import json
import uuid
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context import build_context
from app.errors import ApiError
from app.models import (
    Couple,
    CoupleConstraints,
    CoupleMember,
    Idea,
    ImportantDate,
    PartnerProfile,
    PlannerNote,
    TasteSummary,
    User,
    Vote,
)

pytestmark = pytest.mark.db

TZ = "Pacific/Auckland"


class Fixture:
    couple_id: uuid.UUID
    ann: User
    bea: User
    ann_profile: PartnerProfile
    bea_profile: PartnerProfile


async def seed(session: AsyncSession) -> Fixture:
    fx = Fixture()
    couple = Couple(timezone=TZ, home_area_label="Ponsonby", home_lat=-36.8485, home_lng=174.7633)
    session.add(couple)
    fx.ann, fx.bea = User(email="ann@example.com"), User(email="bea@example.com")
    session.add_all([fx.ann, fx.bea])
    await session.flush()
    fx.couple_id = couple.id
    session.add_all(
        [
            CoupleMember(couple_id=couple.id, user_id=fx.ann.id, role="owner"),
            CoupleMember(couple_id=couple.id, user_id=fx.bea.id, role="partner"),
            CoupleConstraints(couple_id=couple.id, budget_max=120, alcohol="some"),
        ]
    )
    fx.ann_profile = PartnerProfile(
        couple_id=couple.id,
        slot="owner",
        subject_user_id=fx.ann.id,
        display_name="Ann Smith",
        loves="long walks",
    )
    fx.bea_profile = PartnerProfile(
        couple_id=couple.id,
        slot="partner",
        subject_user_id=fx.bea.id,
        display_name="Bea Jones",
        dietary=["vegetarian"],
        allergies=["peanuts"],
        hard_nos="heights",
        loves="pottery",
        energy=2,
    )
    session.add_all([fx.ann_profile, fx.bea_profile])
    await session.commit()
    return fx


def note(fx: Fixture, author: User, about: PartnerProfile, text: str, **kw: Any) -> PlannerNote:
    return PlannerNote(
        couple_id=fx.couple_id,
        author_user_id=author.id,
        about_profile_id=about.id,
        kind="note",
        text=text,
        **kw,
    )


async def context_for(
    session: AsyncSession, fx: Fixture, requester: User, recipient: PartnerProfile
) -> tuple[str, dict[str, Any]]:
    raw = await build_context(session, fx.couple_id, requester.id, recipient.id)
    return raw, json.loads(raw)


async def test_context_has_first_names_constraints_home_and_timezone(
    db_session: AsyncSession,
) -> None:
    fx = await seed(db_session)
    raw, ctx = await context_for(db_session, fx, fx.ann, fx.bea_profile)

    assert ", " not in raw and ": " not in raw  # compact
    assert "Smith" not in raw and "Jones" not in raw  # first names only
    assert ctx["recipient"] == {
        "name": "Bea",
        "energy": 2,
        "loves": "pottery",
        "hard_constraints": {
            "dietary": ["vegetarian"],
            "allergies": ["peanuts"],
            "hard_nos": "heights",
        },
    }
    assert ctx["planner"]["name"] == "Ann"
    couple = ctx["couple"]
    assert couple["timezone"] == TZ
    assert couple["home"] == {"label": "Ponsonby", "lat": -36.85, "lng": 174.76}
    assert "36.8485" not in raw and "174.7633" not in raw  # coarse coordinates only
    assert couple["constraints"] == {"budget_max": 120.0, "currency": "USD", "alcohol": "some"}
    assert couple["today"] == dt.datetime.now(ZoneInfo(TZ)).date().isoformat()


async def test_unnamed_partner_is_called_partner(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    fx.bea_profile.display_name = None
    await db_session.commit()
    _, ctx = await context_for(db_session, fx, fx.ann, fx.bea_profile)
    assert ctx["recipient"]["name"] == "Partner"


async def test_no_home_area_is_null(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    couple = await db_session.get(Couple, fx.couple_id)
    assert couple is not None
    couple.home_lat = couple.home_lng = None
    await db_session.commit()
    _, ctx = await context_for(db_session, fx, fx.ann, fx.bea_profile)
    assert ctx["couple"]["home"] is None


async def test_only_the_requesters_own_notes_about_the_recipient_appear(
    db_session: AsyncSession,
) -> None:
    fx = await seed(db_session)
    db_session.add_all(
        [
            note(fx, fx.ann, fx.bea_profile, "ANN-NOTE-ABOUT-BEA"),
            PlannerNote(
                couple_id=fx.couple_id,
                author_user_id=fx.ann.id,
                about_profile_id=fx.bea_profile.id,
                kind="mentioned_idea",
                text="ANN-MENTIONED-IDEA",
            ),
            note(
                fx, fx.ann, fx.bea_profile, "ANN-ARCHIVED-NOTE", archived_at=dt.datetime.now(dt.UTC)
            ),
            note(fx, fx.bea, fx.ann_profile, "BEA-NOTE-ABOUT-ANN"),
            note(fx, fx.bea, fx.bea_profile, "BEA-NOTE-ABOUT-HERSELF"),
        ]
    )
    await db_session.commit()

    raw, ctx = await context_for(db_session, fx, fx.ann, fx.bea_profile)
    # (Rows from one transaction share a created_at, so compare without relying on order.)
    assert sorted(n["text"] for n in ctx["notes_about_recipient"]) == [
        "ANN-MENTIONED-IDEA",
        "ANN-NOTE-ABOUT-BEA",
    ]
    assert {n["kind"] for n in ctx["notes_about_recipient"]} == {"note", "mentioned_idea"}
    for hidden in ("BEA-NOTE-ABOUT-ANN", "BEA-NOTE-ABOUT-HERSELF", "ANN-ARCHIVED-NOTE"):
        assert hidden not in raw

    # Seen from the other side, Ann's notes are the ones that stay out.
    raw, ctx = await context_for(db_session, fx, fx.bea, fx.ann_profile)
    assert ctx["notes_about_recipient"] == [{"kind": "note", "text": "BEA-NOTE-ABOUT-ANN"}]
    for hidden in ("ANN-NOTE-ABOUT-BEA", "ANN-MENTIONED-IDEA", "BEA-NOTE-ABOUT-HERSELF"):
        assert hidden not in raw


async def test_occasions_in_the_next_60_days_only(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    today = dt.datetime.now(ZoneInfo(TZ)).date()

    def important(name: str, days: int, *, recurring: bool, years_back: int = 0) -> ImportantDate:
        day = today + dt.timedelta(days=days)
        return ImportantDate(
            couple_id=fx.couple_id,
            name=name,
            date=day.replace(year=day.year - years_back) if day.month != 2 else day,
            recurring=recurring,
            kind="custom",
            created_by_user_id=fx.ann.id,
        )

    db_session.add_all(
        [
            important("Soon, recurring", 10, recurring=True, years_back=3),
            important("Edge", 60, recurring=True, years_back=1),
            important("Too far", 61, recurring=False),
            important("Yesterday", -1, recurring=False),
            important("Today", 0, recurring=False),
        ]
    )
    await db_session.commit()

    _, ctx = await context_for(db_session, fx, fx.ann, fx.bea_profile)
    names = [(o["name"], o["days_away"]) for o in ctx["occasions"]]
    assert names == [("Today", 0), ("Soon, recurring", 10), ("Edge", 60)]


async def test_taste_summary_and_idea_titles(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    db_session.add(
        TasteSummary(
            couple_id=fx.couple_id,
            about_profile_id=fx.bea_profile.id,
            summary={"likes": ["quiet bars"]},
        )
    )

    def idea(title: str, by: User, *, visibility: str = "shared", status: str = "active") -> Idea:
        return Idea(
            couple_id=fx.couple_id,
            created_by_user_id=by.id,
            source="manual",
            visibility=visibility,
            title=title,
            pitch="p",
            status=status,
        )

    escape = idea("Escape room", fx.bea)
    db_session.add_all(
        [
            idea("Rooftop picnic", fx.bea),
            idea("Ann's private idea", fx.ann, visibility="private"),
            idea("Bea's private surprise for Ann", fx.bea, visibility="private"),
            idea("Old bowling", fx.ann, status="archived"),
            idea("Lapsed gala", fx.ann, status="expired"),
            escape,
        ]
    )
    await db_session.flush()
    # Bea voted no and Ann voted too, so Ann can see it.
    db_session.add(Vote(idea_id=escape.id, user_id=fx.bea.id, value="no"))
    db_session.add(Vote(idea_id=escape.id, user_id=fx.ann.id, value="yes"))
    await db_session.commit()

    raw, ctx = await context_for(db_session, fx, fx.ann, fx.bea_profile)
    assert ctx["taste"] == {"likes": ["quiet bars"]}
    assert "Bea's private surprise for Ann" not in raw  # someone else's private idea
    ideas = ctx["ideas"]
    assert set(ideas["recent"]) == {
        "Rooftop picnic",
        "Ann's private idea",
        "Old bowling",
        "Lapsed gala",
        "Escape room",
    }
    assert set(ideas["active"]) == {"Rooftop picnic", "Ann's private idea", "Escape room"}
    assert set(ideas["passed"]) == {"Old bowling", "Lapsed gala", "Escape room"}

    _, empty = await context_for(db_session, fx, fx.bea, fx.ann_profile)
    assert empty["taste"] is None


async def test_a_partners_no_vote_is_passed_only_once_the_requester_has_voted(
    db_session: AsyncSession,
) -> None:
    fx = await seed(db_session)
    ideas = {
        name: Idea(
            couple_id=fx.couple_id,
            created_by_user_id=fx.bea.id,
            source="manual",
            visibility="shared",
            title=name,
            pitch="p",
        )
        for name in ("Unvoted by Ann", "Ann said yes", "Ann said no")
    }
    db_session.add_all(ideas.values())
    await db_session.flush()
    for idea in ideas.values():
        db_session.add(Vote(idea_id=idea.id, user_id=fx.bea.id, value="no"))
    db_session.add(Vote(idea_id=ideas["Ann said yes"].id, user_id=fx.ann.id, value="yes"))
    db_session.add(Vote(idea_id=ideas["Ann said no"].id, user_id=fx.ann.id, value="no"))
    await db_session.commit()

    raw, ctx = await context_for(db_session, fx, fx.ann, fx.bea_profile)
    # (a) Bea's no is hidden from Ann until Ann has voted; (b) it shows once she has; (c) her
    # own no always does.
    assert set(ctx["ideas"]["passed"]) == {"Ann said yes", "Ann said no"}
    assert "Unvoted by Ann" in ctx["ideas"]["active"]


async def test_exclude_surprises_for_is_accepted_and_changes_nothing_yet(
    db_session: AsyncSession,
) -> None:
    fx = await seed(db_session)
    plain = await build_context(db_session, fx.couple_id, fx.ann.id, fx.bea_profile.id)
    hooked = await build_context(
        db_session, fx.couple_id, fx.ann.id, fx.bea_profile.id, exclude_surprises_for=fx.bea.id
    )
    assert hooked == plain


async def test_a_recipient_outside_the_couple_is_not_found(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    with pytest.raises(ApiError) as caught:
        await build_context(db_session, fx.couple_id, fx.ann.id, uuid.uuid4())
    assert caught.value.status == 404


async def test_a_shared_context_has_no_notes_surprises_private_ideas_or_votes(
    db_session: AsyncSession,
) -> None:
    fx = await seed(db_session)

    def idea(title: str, by: User, **kw: Any) -> Idea:
        values: dict[str, Any] = {
            "couple_id": fx.couple_id,
            "created_by_user_id": by.id,
            "source": "manual",
            "visibility": "shared",
            "title": title,
            "pitch": "p",
        }
        values.update(kw)
        return Idea(**values)

    voted = idea("Voted down", fx.bea)
    db_session.add_all(
        [
            TasteSummary(
                couple_id=fx.couple_id,
                about_profile_id=fx.bea_profile.id,
                summary={"likes": ["quiet bars"]},
            ),
            note(fx, fx.ann, fx.bea_profile, "ANN-NOTE-ABOUT-BEA"),
            idea("Shared deck idea", fx.bea),
            idea("Ann's private idea", fx.ann, visibility="private"),
            idea("Ann's own surprise", fx.ann, visibility="private", is_surprise=True),
            idea("A shared surprise", fx.ann, is_surprise=True),
            voted,
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            Vote(idea_id=voted.id, user_id=fx.bea.id, value="no"),
            Vote(idea_id=voted.id, user_id=fx.ann.id, value="yes"),
        ]
    )
    await db_session.commit()

    solo = json.loads(await build_context(db_session, fx.couple_id, fx.ann.id, fx.bea_profile.id))
    assert solo["notes_about_recipient"]
    assert "Ann's own surprise" in solo["ideas"]["recent"]
    assert "Voted down" in solo["ideas"]["passed"]
    assert solo["taste"] == {"likes": ["quiet bars"]}

    raw = await build_context(db_session, fx.couple_id, fx.ann.id, fx.bea_profile.id, shared=True)
    ctx = json.loads(raw)
    assert ctx["notes_about_recipient"] == []
    assert set(ctx["ideas"]["recent"]) == {"Shared deck idea", "Voted down"}
    assert ctx["ideas"]["passed"] == []
    assert ctx["taste"] is None
    for hidden in ("ANN-NOTE-ABOUT-BEA", "private idea", "surprise", "quiet bars"):
        assert hidden not in raw
    # The constraints and profiles are still there.
    assert ctx["recipient"]["hard_constraints"]["allergies"] == ["peanuts"]


async def test_travel_radius_reaches_the_model_in_miles(db_session: AsyncSession) -> None:
    fx = await seed(db_session)
    row = await db_session.get(CoupleConstraints, fx.couple_id)
    assert row is not None
    row.max_radius_km = 16
    await db_session.commit()
    raw, ctx = await context_for(db_session, fx, fx.ann, fx.bea_profile)
    assert ctx["couple"]["constraints"]["max_radius_miles"] == 10
    assert "max_radius_km" not in raw

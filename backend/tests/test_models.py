"""Schema constraints, defaults and cascades, checked against the real database (marker: db)."""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AvailabilityBlock,
    AvailabilityWindow,
    Couple,
    CoupleConstraints,
    CoupleMember,
    ImportantDate,
    MagicLinkToken,
    PartnerProfile,
    PlannerNote,
    User,
    UserSession,
)

pytestmark = pytest.mark.db

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)


async def expect_violation(session: AsyncSession, *objs: object) -> None:
    """Flushing `objs` must break a constraint. A savepoint keeps the session, and what the
    test has already committed, usable afterwards."""
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add_all(objs)
            await session.flush()


async def make_user(session: AsyncSession, email: str = "ann@example.com") -> User:
    user = User(email=email)
    session.add(user)
    await session.commit()
    return user


async def make_couple(session: AsyncSession) -> Couple:
    couple = Couple()
    session.add(couple)
    await session.commit()
    return couple


async def make_profile(
    session: AsyncSession, couple: Couple, slot: str = "owner"
) -> PartnerProfile:
    profile = PartnerProfile(couple_id=couple.id, slot=slot)
    session.add(profile)
    await session.commit()
    return profile


async def count(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- users and auth


async def test_user_defaults_and_unique_lowercase_email(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    await db_session.refresh(user)
    assert user.timezone == "UTC"
    assert user.notification_settings == {}
    assert user.display_name is None
    assert user.deleted_at is None
    assert user.created_at is not None
    await expect_violation(db_session, User(email="ann@example.com"))


async def test_uppercase_email_is_rejected(db_session: AsyncSession) -> None:
    await expect_violation(db_session, User(email="Ann@Example.com"))


async def test_token_hashes_are_unique(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    db_session.add(MagicLinkToken(email="a@example.com", token_hash="h1", expires_at=NOW))
    db_session.add(UserSession(user_id=user.id, token_hash="h2", expires_at=NOW))
    await db_session.commit()
    await expect_violation(
        db_session, MagicLinkToken(email="b@example.com", token_hash="h1", expires_at=NOW)
    )
    await expect_violation(
        db_session, UserSession(user_id=user.id, token_hash="h2", expires_at=NOW)
    )


async def test_deleting_a_user_with_sessions_is_refused(db_session: AsyncSession) -> None:
    # Users are soft-deleted (deleted_at); no user FK cascades or nulls on a hard delete.
    user = await make_user(db_session)
    db_session.add(UserSession(user_id=user.id, token_hash="h", expires_at=NOW))
    await db_session.commit()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                delete(User).where(User.id == user.id).execution_options(synchronize_session=False)
            )
    assert await count(db_session, UserSession) == 1
    assert await count(db_session, User) == 1


# --- couples and members


async def test_couple_defaults_and_status_check(db_session: AsyncSession) -> None:
    couple = await make_couple(db_session)
    await db_session.refresh(couple)
    assert couple.status == "active"
    assert couple.timezone == "UTC"
    await expect_violation(db_session, Couple(status="deleted"))


async def test_home_coordinates_are_stored_rounded_to_two_decimals(
    db_session: AsyncSession,
) -> None:
    couple = Couple(home_lat=Decimal("30.2672"), home_lng=Decimal("-97.7431"))
    db_session.add(couple)
    await db_session.commit()
    await db_session.refresh(couple)
    assert (couple.home_lat, couple.home_lng) == (Decimal("30.27"), Decimal("-97.74"))
    # The full longitude range fits.
    db_session.add(Couple(home_lat=Decimal("-90"), home_lng=Decimal("-180")))
    await db_session.commit()


async def test_user_can_have_only_one_active_couple(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    first, second = await make_couple(db_session), await make_couple(db_session)
    member = CoupleMember(couple_id=first.id, user_id=user.id, role="owner")
    db_session.add(member)
    await db_session.commit()
    await expect_violation(
        db_session, CoupleMember(couple_id=second.id, user_id=user.id, role="owner")
    )
    # Once they have left, they can join another couple.
    await db_session.execute(
        update(CoupleMember).where(CoupleMember.id == member.id).values(left_at=NOW)
    )
    db_session.add(CoupleMember(couple_id=second.id, user_id=user.id, role="owner"))
    await db_session.commit()


async def test_couple_has_at_most_two_active_members(db_session: AsyncSession) -> None:
    ann, bob, cy = (
        await make_user(db_session, "ann@example.com"),
        await make_user(db_session, "bob@example.com"),
        await make_user(db_session, "cy@example.com"),
    )
    couple = await make_couple(db_session)
    db_session.add(CoupleMember(couple_id=couple.id, user_id=ann.id, role="owner"))
    db_session.add(CoupleMember(couple_id=couple.id, user_id=bob.id, role="partner"))
    await db_session.commit()
    for role in ("owner", "partner"):
        await expect_violation(
            db_session, CoupleMember(couple_id=couple.id, user_id=cy.id, role=role)
        )
    await expect_violation(
        db_session, CoupleMember(couple_id=couple.id, user_id=cy.id, role="boss")
    )


# --- profiles, availability, constraints


async def test_profile_defaults(db_session: AsyncSession) -> None:
    profile = await make_profile(db_session, await make_couple(db_session))
    await db_session.refresh(profile)
    for column in (
        "liked_categories",
        "disliked_categories",
        "cuisines_liked",
        "cuisines_disliked",
        "dietary",
        "allergies",
    ):
        assert getattr(profile, column) == []
    assert profile.subject_user_id is None
    assert profile.energy is None
    assert profile.updated_at is not None


async def test_duplicate_slot_in_a_couple_is_rejected(db_session: AsyncSession) -> None:
    couple = await make_couple(db_session)
    await make_profile(db_session, couple, "owner")
    await expect_violation(db_session, PartnerProfile(couple_id=couple.id, slot="owner"))
    await make_profile(db_session, couple, "partner")


@pytest.mark.parametrize(
    "bad",
    [
        {"slot": "third"},
        {"energy": 0},
        {"energy": 6},
        {"social_setting": 6},
        {"indoor_outdoor": "sideways"},
    ],
)
async def test_profile_check_constraints(db_session: AsyncSession, bad: dict[str, object]) -> None:
    couple = await make_couple(db_session)
    values: dict[str, object] = {"couple_id": couple.id, "slot": "owner"} | bad
    await expect_violation(db_session, PartnerProfile(**values))


async def test_profile_accepts_values_on_the_edge(db_session: AsyncSession) -> None:
    couple = await make_couple(db_session)
    db_session.add(
        PartnerProfile(
            couple_id=couple.id, slot="owner", energy=1, social_setting=5, indoor_outdoor="either"
        )
    )
    await db_session.commit()


async def test_availability_window_checks(db_session: AsyncSession) -> None:
    profile = await make_profile(db_session, await make_couple(db_session))
    nine, five = dt.time(9), dt.time(17)
    db_session.add(
        AvailabilityWindow(profile_id=profile.id, weekday=6, start_time=nine, end_time=five)
    )
    await db_session.commit()
    for bad in (
        {"weekday": 1, "start_time": five, "end_time": nine},
        {"weekday": 1, "start_time": nine, "end_time": nine},
        {"weekday": 7, "start_time": nine, "end_time": five},
        {"weekday": -1, "start_time": nine, "end_time": five},
    ):
        await expect_violation(db_session, AvailabilityWindow(profile_id=profile.id, **bad))


async def test_alcohol_and_date_kind_checks(db_session: AsyncSession) -> None:
    ann = await make_user(db_session)
    couple = await make_couple(db_session)
    await expect_violation(db_session, CoupleConstraints(couple_id=couple.id, alcohol="lots"))
    db_session.add(CoupleConstraints(couple_id=couple.id, alcohol="some"))
    await db_session.commit()
    row = await db_session.get(CoupleConstraints, couple.id)
    assert row is not None
    await db_session.refresh(row)
    assert (row.currency, row.transport_modes) == ("USD", [])
    # One row per couple: the couple id is the primary key.
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(insert(CoupleConstraints).values(couple_id=couple.id))
    await expect_violation(
        db_session,
        ImportantDate(
            couple_id=couple.id,
            name="x",
            date=NOW.date(),
            kind="holiday",
            created_by_user_id=ann.id,
        ),
    )


async def test_note_kind_check(db_session: AsyncSession) -> None:
    ann = await make_user(db_session)
    couple = await make_couple(db_session)
    profile = await make_profile(db_session, couple)
    note = {"couple_id": couple.id, "author_user_id": ann.id, "about_profile_id": profile.id}
    await expect_violation(db_session, PlannerNote(**note, kind="rant", text="x"))
    db_session.add(PlannerNote(**note, kind="mentioned_idea", text="pottery class"))
    await db_session.commit()


async def test_deleting_a_couple_cascades_to_everything_under_it(db_session: AsyncSession) -> None:
    ann = await make_user(db_session)
    couple = await make_couple(db_session)
    profile = await make_profile(db_session, couple)
    db_session.add_all(
        [
            CoupleMember(couple_id=couple.id, user_id=ann.id, role="owner"),
            CoupleConstraints(couple_id=couple.id),
            AvailabilityWindow(
                profile_id=profile.id, weekday=0, start_time=dt.time(9), end_time=dt.time(10)
            ),
            AvailabilityBlock(profile_id=profile.id, date=NOW.date(), note="away"),
            ImportantDate(
                couple_id=couple.id,
                name="Anniversary",
                date=NOW.date(),
                kind="anniversary",
                created_by_user_id=ann.id,
            ),
            PlannerNote(
                couple_id=couple.id,
                author_user_id=ann.id,
                about_profile_id=profile.id,
                kind="note",
                text="likes tea",
            ),
        ]
    )
    await db_session.commit()
    await db_session.execute(
        delete(Couple).where(Couple.id == couple.id).execution_options(synchronize_session=False)
    )
    await db_session.commit()
    for model in (
        CoupleMember,
        PartnerProfile,
        CoupleConstraints,
        AvailabilityWindow,
        AvailabilityBlock,
        ImportantDate,
        PlannerNote,
    ):
        assert await count(db_session, model) == 0, model.__name__
    assert await count(db_session, User) == 1

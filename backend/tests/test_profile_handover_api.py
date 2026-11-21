"""Profile hand-over (FR-PROF-7): the subject confirms a profile, the other member suggests."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update

from app.models import ProfileSuggestion
from app.schemas.profiles import ProfileChanges, ProfileIn
from tests.authutil import browser, call, joined_couple, profile_ids, sign_in

pytestmark = pytest.mark.db

API = "/api/v1/profiles"


def suggest(changes: dict[str, Any], note: str | None = None) -> dict[str, Any]:
    return {"changes": changes, "note": note}


async def make_suggestion(
    client: AsyncClient, profile: str, changes: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await call(
        client, "POST", f"{API}/{profile}/suggestions", suggest(changes or {"loves": "Jazz"})
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_profile_changes_has_exactly_the_profile_fields() -> None:
    assert set(ProfileChanges.model_fields) == set(ProfileIn.model_fields)


async def test_the_subject_confirms_and_repeating_it_changes_nothing(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        profile = (await profile_ids(ann))["partner"]
        first = await call(bea, "POST", f"{API}/{profile}/confirm")
        assert first.status_code == 200
        stamp = first.json()["confirmed_by_subject_at"]
        assert stamp is not None and first.json()["is_me"] is True
        again = await call(bea, "POST", f"{API}/{profile}/confirm")
        assert again.status_code == 200
        assert again.json()["confirmed_by_subject_at"] == stamp
        assert (await ann.get(f"{API}/{profile}")).json()["confirmed_by_subject_at"] == stamp


async def test_only_the_subject_may_confirm(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, _bea):
        profile = (await profile_ids(ann))["partner"]
        denied = await call(ann, "POST", f"{API}/{profile}/confirm")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "forbidden"
        assert (await ann.get(f"{API}/{profile}")).json()["confirmed_by_subject_at"] is None


async def test_the_author_suggests_and_the_subject_lists_it(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        profile = (await profile_ids(ann))["partner"]
        response = await call(
            ann,
            "POST",
            f"{API}/{profile}/suggestions",
            suggest({"loves": "Jazz", "energy": 4, "liked_categories": ["food", "food"]}, "  Hi "),
        )
        assert response.status_code == 201
        made = response.json()
        assert made["changes"] == {"loves": "Jazz", "energy": 4, "liked_categories": ["food"]}
        assert made["note"] == "Hi" and made["status"] == "pending"
        assert made["mine"] is True and made["profile_id"] == profile
        assert made["decided_at"] is None and made["created_at"]

        listed = (await bea.get(f"{API}/{profile}/suggestions")).json()
        assert [s["id"] for s in listed] == [made["id"]]
        assert listed[0]["mine"] is False
        assert (await bea.get(f"{API}/{profile}")).json()["loves"] is None  # not applied yet


async def test_a_blank_note_is_stored_as_none(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, _bea):
        profile = (await profile_ids(ann))["partner"]
        response = await call(
            ann, "POST", f"{API}/{profile}/suggestions", suggest({"loves": "Jazz"}, "   ")
        )
        assert response.status_code == 201 and response.json()["note"] is None


async def test_listing_is_newest_first_and_filters_by_status(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        profile = (await profile_ids(ann))["partner"]
        first = await make_suggestion(ann, profile, {"loves": "One"})
        second = await make_suggestion(ann, profile, {"loves": "Two"})
        decline = await call(bea, "POST", f"{API}/{profile}/suggestions/{first['id']}/decline")
        assert decline.status_code == 200
        everything = (await bea.get(f"{API}/{profile}/suggestions")).json()
        assert [s["id"] for s in everything] == [second["id"], first["id"]]
        for status, expected in (("pending", second), ("declined", first)):
            rows = (await bea.get(f"{API}/{profile}/suggestions?status={status}")).json()
            assert [s["id"] for s in rows] == [expected["id"]]
        assert (await bea.get(f"{API}/{profile}/suggestions?status=accepted")).json() == []
        assert (await bea.get(f"{API}/{profile}/suggestions?status=bogus")).status_code == 422


async def test_the_author_sees_only_their_own_and_a_stranger_gets_404(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea), browser(db_app) as cat:
        await sign_in(db_app, cat, "cat@example.com")
        profile = (await profile_ids(ann))["partner"]
        mine = await make_suggestion(ann, profile)
        # The subject herself cannot suggest, so plant a second author's row directly.
        async with db_app.state.sessionmaker() as session:
            session.add(
                ProfileSuggestion(
                    profile_id=uuid.UUID(profile),
                    author_user_id=uuid.UUID((await bea.get("/api/v1/me")).json()["id"]),
                    changes={"loves": "Tea"},
                )
            )
            await session.commit()
        assert len((await bea.get(f"{API}/{profile}/suggestions")).json()) == 2
        assert [s["id"] for s in (await ann.get(f"{API}/{profile}/suggestions")).json()] == [
            mine["id"]
        ]

        body = suggest({"loves": "Jazz"})
        assert (await cat.get(f"{API}/{profile}/suggestions")).status_code == 404
        assert (await call(cat, "POST", f"{API}/{profile}/suggestions", body)).status_code == 404
        assert (await call(cat, "POST", f"{API}/{profile}/confirm")).status_code == 404
        for verb in ("accept", "decline"):
            url = f"{API}/{profile}/suggestions/{mine['id']}/{verb}"
            assert (await call(cat, "POST", url)).status_code == 404


async def test_accept_applies_the_changes_as_the_subject_then_409s(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        profile = (await profile_ids(ann))["partner"]
        # Bea fills in some of her own profile first; the suggestion must keep it.
        put = await call(
            bea, "PUT", f"{API}/{profile}", {"display_name": "Bea", "allergies": ["peanuts"]}
        )
        assert put.status_code == 200
        bea_id = (await bea.get("/api/v1/me")).json()["id"]
        made = await make_suggestion(ann, profile, {"loves": "Jazz", "energy": 5})
        url = f"{API}/{profile}/suggestions/{made['id']}"

        accepted = await call(bea, "POST", f"{url}/accept")
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["loves"] == "Jazz" and body["energy"] == 5
        assert body["display_name"] == "Bea" and body["allergies"] == ["peanuts"]  # kept
        assert body["edited_by_user_id"] == bea_id
        assert (await bea.get(f"{API}/{profile}")).json() == body

        row = (await bea.get(f"{API}/{profile}/suggestions")).json()[0]
        assert row["status"] == "accepted" and row["decided_at"] is not None
        for verb in ("accept", "decline"):
            again = await call(bea, "POST", f"{url}/{verb}")
            assert again.status_code == 409
            assert again.json()["error"]["code"] == "already_decided"


async def test_decline_leaves_the_profile_unchanged(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        profile = (await profile_ids(ann))["partner"]
        before = (await bea.get(f"{API}/{profile}")).json()
        made = await make_suggestion(ann, profile)
        declined = await call(bea, "POST", f"{API}/{profile}/suggestions/{made['id']}/decline")
        assert declined.status_code == 200
        assert declined.json()["status"] == "declined" and declined.json()["decided_at"]
        assert (await bea.get(f"{API}/{profile}")).json() == before


async def test_only_the_subject_decides_and_unknown_suggestions_are_404(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        ids = await profile_ids(ann)
        made = await make_suggestion(ann, ids["partner"])
        url = f"{API}/{ids['partner']}/suggestions/{made['id']}"
        for verb in ("accept", "decline"):
            denied = await call(ann, "POST", f"{url}/{verb}")
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "forbidden"
        ghost = f"{API}/{ids['partner']}/suggestions/{uuid.uuid4()}"
        # A suggestion id that belongs to a different profile is also 404.
        elsewhere = f"{API}/{ids['owner']}/suggestions/{made['id']}"
        for verb in ("accept", "decline"):
            assert (await call(bea, "POST", f"{ghost}/{verb}")).status_code == 404
            assert (await call(ann, "POST", f"{elsewhere}/{verb}")).status_code == 404
        assert (await bea.get(f"{API}/{ids['partner']}/suggestions")).json()[0]["status"] == (
            "pending"
        )


async def test_suggesting_where_you_can_edit_directly_is_409(db_app: FastAPI) -> None:
    async with browser(db_app) as ann:
        await sign_in(db_app, ann, "ann@example.com")
        ids = await profile_ids(ann)
        for slot in ("owner", "partner"):  # her own, and the partner's before she joins
            response = await call(
                ann, "POST", f"{API}/{ids[slot]}/suggestions", suggest({"loves": "Jazz"})
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "edit_directly"
    async with joined_couple(db_app, "cat@example.com", "dan@example.com") as (_cat, dan):
        own = (await profile_ids(dan))["partner"]
        response = await call(dan, "POST", f"{API}/{own}/suggestions", suggest({"loves": "Jazz"}))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "edit_directly"


@pytest.mark.parametrize(
    "body",
    [
        {"changes": {}},
        {},
        {"changes": {"energy": 9}},
        {"changes": {"nope": 1}},
        {"changes": {"liked_categories": None}},
        {"changes": {"loves": "x"}, "note": "n" * 501},
    ],
)
async def test_an_invalid_suggestion_is_422(db_app: FastAPI, body: dict[str, Any]) -> None:
    async with joined_couple(db_app) as (ann, _bea):
        profile = (await profile_ids(ann))["partner"]
        response = await call(ann, "POST", f"{API}/{profile}/suggestions", body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


async def test_a_null_is_allowed_where_the_profile_allows_it(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        profile = (await profile_ids(ann))["partner"]
        made = await make_suggestion(ann, profile, {"loves": None})
        assert made["changes"] == {"loves": None}
        accepted = await call(bea, "POST", f"{API}/{profile}/suggestions/{made['id']}/accept")
        assert accepted.status_code == 200 and accepted.json()["loves"] is None


async def test_the_21st_pending_suggestion_is_409(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        profile = (await profile_ids(ann))["partner"]
        first = await make_suggestion(ann, profile, {"loves": "0"})
        for i in range(1, 20):
            await make_suggestion(ann, profile, {"loves": str(i)})
        full = await call(ann, "POST", f"{API}/{profile}/suggestions", suggest({"loves": "x"}))
        assert full.status_code == 409
        assert full.json()["error"]["code"] == "too_many_suggestions"
        # Deciding one frees a slot: only pending ones count.
        await call(bea, "POST", f"{API}/{profile}/suggestions/{first['id']}/decline")
        assert (
            await call(ann, "POST", f"{API}/{profile}/suggestions", suggest({"loves": "y"}))
        ).status_code == 201


async def test_accepting_now_invalid_data_is_422_and_stays_pending(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (ann, bea):
        profile = (await profile_ids(ann))["partner"]
        made = await make_suggestion(ann, profile)
        async with db_app.state.sessionmaker() as session:
            await session.execute(
                update(ProfileSuggestion)
                .where(ProfileSuggestion.id == uuid.UUID(made["id"]))
                .values(changes={"energy": 99})
            )
            await session.commit()
        response = await call(bea, "POST", f"{API}/{profile}/suggestions/{made['id']}/accept")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        listed = (await bea.get(f"{API}/{profile}/suggestions")).json()
        assert [s["status"] for s in listed] == ["pending"]
        body = (await bea.get(f"{API}/{profile}")).json()
        assert body["energy"] is None and body["edited_by_user_id"] is None
        async with db_app.state.sessionmaker() as session:
            row = (await session.execute(select(ProfileSuggestion))).scalar_one()
            assert row.decided_at is None

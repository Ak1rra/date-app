"""Partner profiles and availability: who can read, who can write."""

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AvailabilityBlock, AvailabilityWindow
from tests.authutil import call, joined_couple, profile_ids, sign_in

pytestmark = pytest.mark.db

PROFILES = "/api/v1/profiles"

FULL_PROFILE: dict[str, Any] = {
    "display_name": "Ann",
    "liked_categories": ["food", "music", "food"],
    "disliked_categories": ["nightlife"],
    "cuisines_liked": ["thai", " ramen "],
    "cuisines_disliked": ["liver"],
    "energy": 4,
    "indoor_outdoor": "either",
    "social_setting": 2,
    "loves": "Live jazz",
    "hard_nos": "Crowded bars",
    "dietary": ["vegetarian", "gluten_free"],
    "allergies": ["peanuts"],
    "accessibility": "No stairs",
}


async def test_list_profiles_returns_both_slots_owner_first(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    user = (await sign_in(db_app, db_client, "ann@example.com")).json()["user"]
    response = await db_client.get(PROFILES)
    assert response.status_code == 200
    owner, partner = response.json()
    assert (owner["slot"], partner["slot"]) == ("owner", "partner")
    assert owner["is_me"] is True and partner["is_me"] is False
    assert owner["subject_joined"] is True and partner["subject_joined"] is False
    assert owner["can_edit"] is True and partner["can_edit"] is True  # unclaimed: planner edits
    for profile in (owner, partner):
        assert profile["liked_categories"] == [] and profile["allergies"] == []
        assert profile["energy"] is None and profile["edited_by_user_id"] is None
        assert profile["confirmed_by_subject_at"] is None
    assert (await db_client.get(f"{PROFILES}/{owner['id']}")).json() == owner
    assert user["id"]


async def test_put_profile_replaces_and_stamps_the_editor(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    user = (await sign_in(db_app, db_client, "ann@example.com")).json()["user"]
    before = (await db_client.get(PROFILES)).json()[0]
    response = await call(db_client, "PUT", f"{PROFILES}/{before['id']}", FULL_PROFILE)
    assert response.status_code == 200
    body = response.json()
    assert body["liked_categories"] == ["food", "music"]  # deduped, order kept
    assert body["cuisines_liked"] == ["thai", "ramen"]  # stripped
    assert body["energy"] == 4 and body["indoor_outdoor"] == "either"
    assert body["dietary"] == ["vegetarian", "gluten_free"]
    assert body["edited_by_user_id"] == user["id"]
    assert body["updated_at"] > before["updated_at"]
    assert body["is_me"] is True and body["can_edit"] is True
    assert (await db_client.get(f"{PROFILES}/{before['id']}")).json() == body

    # Full replace: a second PUT without the lists and scales clears them.
    cleared = (await call(db_client, "PUT", f"{PROFILES}/{before['id']}", {"loves": "Tea"})).json()
    assert cleared["loves"] == "Tea"
    assert cleared["liked_categories"] == [] and cleared["energy"] is None
    assert cleared["display_name"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"liked_categories": ["pottery"]},
        {"disliked_categories": "food"},
        {"dietary": ["carnivore"]},
        {"energy": 0},
        {"energy": 6},
        {"social_setting": 6},
        {"indoor_outdoor": "underwater"},
        {"loves": "x" * 2001},
        {"allergies": ["x" * 41]},
        {"allergies": [""]},
        {"cuisines_liked": [f"c{i}" for i in range(31)]},
    ],
)
async def test_put_profile_validates(
    db_app: FastAPI, db_client: AsyncClient, body: dict[str, Any]
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    owner = (await profile_ids(db_client))["owner"]
    response = await call(db_client, "PUT", f"{PROFILES}/{owner}", body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_the_planner_can_edit_an_unclaimed_partner_profile(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    user = (await sign_in(db_app, db_client, "ann@example.com")).json()["user"]
    partner = (await profile_ids(db_client))["partner"]
    response = await call(
        db_client,
        "PUT",
        f"{PROFILES}/{partner}",
        {"display_name": "Bea", "liked_categories": ["arts"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_me"] is False and body["subject_joined"] is False and body["can_edit"] is True
    assert body["edited_by_user_id"] == user["id"]
    assert body["liked_categories"] == ["arts"]


async def test_a_joined_partner_profile_is_read_only_for_the_owner(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        ids = await profile_ids(owner)
        seen = (await owner.get(f"{PROFILES}/{ids['partner']}")).json()
        assert seen["subject_joined"] is True
        assert seen["is_me"] is False and seen["can_edit"] is False

        denied = await call(owner, "PUT", f"{PROFILES}/{ids['partner']}", FULL_PROFILE)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "forbidden"
        assert (await owner.get(f"{PROFILES}/{ids['partner']}")).json() == seen  # unchanged

        # She edits her own profile, and the owner then sees her answers.
        mine = (await partner.get(PROFILES)).json()
        assert [p["is_me"] for p in mine] == [False, True]
        assert [p["can_edit"] for p in mine] == [False, True]
        edited = await call(partner, "PUT", f"{PROFILES}/{ids['partner']}", FULL_PROFILE)
        assert edited.status_code == 200
        partner_id = (await partner.get("/api/v1/me")).json()["id"]
        assert edited.json()["edited_by_user_id"] == partner_id
        assert (await owner.get(f"{PROFILES}/{ids['partner']}")).json()["loves"] == "Live jazz"

        # And she cannot change his.
        assert (
            await call(partner, "PUT", f"{PROFILES}/{ids['owner']}", FULL_PROFILE)
        ).status_code == 403
        assert (
            await call(owner, "PUT", f"{PROFILES}/{ids['owner']}", FULL_PROFILE)
        ).status_code == 200


# -- availability -----------------------------------------------------------------------------

AVAILABILITY: dict[str, Any] = {
    "windows": [
        {"weekday": 5, "start_time": "10:00:00", "end_time": "22:00:00"},
        {"weekday": 1, "start_time": "18:30:00", "end_time": "21:00:00"},
    ],
    "blocks": [
        {"date": "2030-12-24", "note": "Family"},
        {"date": "2030-06-01"},
    ],
}


async def test_availability_starts_empty_and_put_replaces_all(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    ids = await profile_ids(db_client)
    url = f"{PROFILES}/{ids['partner']}/availability"
    assert (await db_client.get(url)).json() == {"windows": [], "blocks": []}

    response = await call(db_client, "PUT", url, AVAILABILITY)
    assert response.status_code == 200
    body = response.json()
    assert body["windows"] == [  # sorted by weekday
        {"weekday": 1, "start_time": "18:30:00", "end_time": "21:00:00"},
        {"weekday": 5, "start_time": "10:00:00", "end_time": "22:00:00"},
    ]
    assert body["blocks"] == [  # sorted by date
        {"date": "2030-06-01", "note": None},
        {"date": "2030-12-24", "note": "Family"},
    ]
    assert (await db_client.get(url)).json() == body

    # Replace-all: the second PUT leaves only what it names, and the other profile alone.
    other = f"{PROFILES}/{ids['owner']}/availability"
    await call(
        db_client,
        "PUT",
        other,
        {"windows": [{"weekday": 0, "start_time": "08:00", "end_time": "09:00"}]},
    )
    second = {"windows": [{"weekday": 6, "start_time": "09:00", "end_time": "12:00"}], "blocks": []}
    assert (await call(db_client, "PUT", url, second)).status_code == 200
    again = (await db_client.get(url)).json()
    assert [w["weekday"] for w in again["windows"]] == [6] and again["blocks"] == []
    assert len((await db_client.get(other)).json()["windows"]) == 1
    await db_session.rollback()
    assert len((await db_session.execute(select(AvailabilityWindow))).scalars().all()) == 2
    assert (await db_session.execute(select(AvailabilityBlock))).scalars().all() == []

    # An empty body clears everything.
    assert (await call(db_client, "PUT", url, {})).json() == {"windows": [], "blocks": []}


@pytest.mark.parametrize(
    "body",
    [
        {"windows": [{"weekday": 7, "start_time": "09:00", "end_time": "10:00"}]},
        {"windows": [{"weekday": 0, "start_time": "10:00", "end_time": "10:00"}]},
        {"windows": [{"weekday": 0, "start_time": "11:00", "end_time": "10:00"}]},
        {"windows": [{"weekday": 0, "start_time": "09:00+02:00", "end_time": "10:00"}]},
        {"windows": [{"weekday": 0, "start_time": "nine", "end_time": "10:00"}]},
        {"blocks": [{"date": "tomorrow"}]},
    ],
)
async def test_put_availability_validates_and_keeps_the_old_data(
    db_app: FastAPI, db_client: AsyncClient, body: dict[str, Any]
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    url = f"{PROFILES}/{(await profile_ids(db_client))['owner']}/availability"
    first = (await call(db_client, "PUT", url, AVAILABILITY)).json()
    assert (await call(db_client, "PUT", url, body)).status_code == 422
    assert (await db_client.get(url)).json() == first


async def test_availability_follows_the_profile_edit_rule(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        ids = await profile_ids(owner)
        url = f"{PROFILES}/{ids['partner']}/availability"
        denied = await call(owner, "PUT", url, AVAILABILITY)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "forbidden"
        assert (await owner.get(url)).json() == {"windows": [], "blocks": []}  # readable

        assert (await call(partner, "PUT", url, AVAILABILITY)).status_code == 200
        assert len((await owner.get(url)).json()["windows"]) == 2
        denied_again = await call(
            partner, "PUT", f"{PROFILES}/{ids['owner']}/availability", AVAILABILITY
        )
        assert denied_again.status_code == 403

"""The couple space: couple, home area, constraints and important dates."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_app
from app.models import Couple, CoupleConstraints, CoupleMember, ImportantDate
from app.providers.geocoding.base import GeocodeNotFound, GeocodeResult
from app.providers.geocoding.fake import FakeGeocoder
from app.providers.registry import get_geocoder
from app.providers.types import ProviderUnavailable
from app.services.couple import refresh_home_if_stale
from tests.authutil import browser, call, joined_couple, profile_ids, sign_in
from tests.conftest import make_settings

pytestmark = pytest.mark.db

COUPLE = "/api/v1/couple"
HOME = f"{COUPLE}/home-area"
CONSTRAINTS = f"{COUPLE}/constraints"
DATES = f"{COUPLE}/important-dates"


async def signed_in(app: FastAPI, client: AsyncClient) -> dict[str, Any]:
    return dict((await sign_in(app, client, "ann@example.com")).json()["user"])


async def test_get_couple_for_a_new_account(db_app: FastAPI, db_client: AsyncClient) -> None:
    user = await signed_in(db_app, db_client)
    response = await db_client.get(COUPLE)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user["couple_id"]
    assert body["name"] is None
    assert body["status"] == "active"
    assert body["home_area"] is None
    assert body["partner_joined"] is False
    assert body["members"] == [
        {"user_id": user["id"], "display_name": user["display_name"], "role": "owner"}
    ]


async def test_get_couple_lists_both_members_owner_first(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        body = (await partner.get(COUPLE)).json()
        assert [m["role"] for m in body["members"]] == ["owner", "partner"]
        assert body["partner_joined"] is True
        assert (await owner.get(COUPLE)).json() == body


async def test_patch_couple(db_app: FastAPI, db_client: AsyncClient) -> None:
    await signed_in(db_app, db_client)
    response = await call(
        db_client, "PATCH", COUPLE, {"name": "  Us  ", "timezone": "Europe/Paris"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Us"
    assert response.json()["timezone"] == "Europe/Paris"
    # Fields left out stay as they are.
    only_name = await call(db_client, "PATCH", COUPLE, {"name": "Duo"})
    assert only_name.json()["timezone"] == "Europe/Paris"
    assert (await db_client.get(COUPLE)).json() == only_name.json()


async def test_patch_couple_rejects_explicit_nulls_and_the_schema_says_so(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await signed_in(db_app, db_client)
    await call(db_client, "PATCH", COUPLE, {"name": "Us", "timezone": "Europe/Paris"})
    for body in ({"name": None}, {"timezone": None}):
        assert (await call(db_client, "PATCH", COUPLE, body)).status_code == 422
    unchanged = (await db_client.get(COUPLE)).json()
    assert (unchanged["name"], unchanged["timezone"]) == ("Us", "Europe/Paris")
    props = (await db_client.get("/api/v1/openapi.json")).json()["components"]["schemas"][
        "CouplePatch"
    ]["properties"]
    assert all("anyOf" not in prop for prop in props.values())


@pytest.mark.parametrize(
    "body", [{"timezone": "Mars/Olympus"}, {"timezone": 5}, {"name": ""}, {"name": "x" * 121}]
)
async def test_patch_couple_validates(
    db_app: FastAPI, db_client: AsyncClient, body: dict[str, Any]
) -> None:
    await signed_in(db_app, db_client)
    response = await call(db_client, "PATCH", COUPLE, body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_user_without_a_couple_gets_404(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await signed_in(db_app, db_client)
    await db_session.execute(update(CoupleMember).values(left_at=func.now()))
    await db_session.commit()
    for method, url, body in [
        ("GET", COUPLE, None),
        ("GET", CONSTRAINTS, None),
        ("GET", DATES, None),
        ("GET", "/api/v1/profiles", None),
        ("GET", "/api/v1/notes", None),
        ("PATCH", COUPLE, {"name": "x"}),
    ]:
        response = await call(db_client, method, url, body)
        assert response.status_code == 404, (method, url)
        assert response.json()["error"]["code"] == "not_found"


# -- home area --------------------------------------------------------------------------------


async def test_put_home_area_stores_label_and_coarse_coordinates(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await signed_in(db_app, db_client)
    response = await call(db_client, "PUT", HOME, {"query": "  Zilker Park  "})
    assert response.status_code == 200
    area = response.json()
    assert set(area) == {"label", "lat", "lng"}
    assert area["lat"] == round(area["lat"], 2)
    assert area["lng"] == round(area["lng"], 2)
    assert (await db_client.get(COUPLE)).json()["home_area"] == area

    (couple,) = (await db_session.execute(select(Couple))).scalars().all()
    assert couple.home_area_label == area["label"] == "Zilker Park"  # the user's words, stripped
    assert area["label"] != (await FakeGeocoder().geocode("Zilker Park")).label
    assert (float(couple.home_lat), float(couple.home_lng)) == (area["lat"], area["lng"])
    assert couple.home_place_id is not None
    assert couple.home_geocoded_at is not None

    # Searching again replaces the stored place.
    second = (await call(db_client, "PUT", HOME, {"query": "Mueller"})).json()
    assert second["label"] == "Mueller"
    assert second != area
    assert (await db_client.get(COUPLE)).json()["home_area"] == second


@pytest.mark.parametrize("query", ["", " a ", "x" * 201, 5])
async def test_put_home_area_validates_the_query(
    db_app: FastAPI, db_client: AsyncClient, query: object
) -> None:
    await signed_in(db_app, db_client)
    response = await call(db_client, "PUT", HOME, {"query": query})
    assert response.status_code == 422
    assert (await db_client.get(COUPLE)).json()["home_area"] is None


class FailingGeocoder:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def geocode(self, query: str) -> GeocodeResult:
        raise self.error


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (GeocodeNotFound(), 404, "not_found"),
        (ProviderUnavailable("google_geocode", "over_quota"), 503, "provider_unavailable"),
        (ProviderUnavailable("google_geocode", "bad_response"), 503, "provider_unavailable"),
    ],
)
async def test_put_home_area_turns_geocoder_failures_into_api_errors(
    db_app: FastAPI, db_client: AsyncClient, error: Exception, status: int, code: str
) -> None:
    await signed_in(db_app, db_client)
    db_app.dependency_overrides[get_geocoder] = lambda: FailingGeocoder(error)
    response = await call(db_client, "PUT", HOME, {"query": "Austin"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert (await db_client.get(COUPLE)).json()["home_area"] is None


async def stale_home(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession, age: timedelta
) -> Couple:
    """A couple whose home area was geocoded `age` ago, from a place the fake geocoder gave."""
    await signed_in(db_app, db_client)
    await call(db_client, "PUT", HOME, {"query": "Zilker Park"})
    stamp = datetime.now(UTC) - age
    await db_session.execute(
        update(Couple).values(home_geocoded_at=stamp, home_lat=1, home_lng=1, home_place_id="old")
    )
    await db_session.commit()
    (couple,) = (await db_session.execute(select(Couple))).scalars().all()
    await db_session.refresh(couple)
    return couple


async def test_a_stale_home_area_is_re_geocoded_and_keeps_its_label(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    couple = await stale_home(db_app, db_client, db_session, timedelta(days=31))
    await refresh_home_if_stale(db_session, FakeGeocoder(), couple.id)
    found = await FakeGeocoder().geocode("Zilker Park")
    assert couple.home_area_label == "Zilker Park"
    assert (float(couple.home_lat), float(couple.home_lng)) == (
        round(found.lat, 2),
        round(found.lng, 2),
    )
    assert couple.home_place_id == found.place_id
    assert couple.home_geocoded_at is not None
    assert datetime.now(UTC) - couple.home_geocoded_at < timedelta(minutes=1)


async def test_a_fresh_home_area_is_left_alone(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    couple = await stale_home(db_app, db_client, db_session, timedelta(days=29))
    before = couple.home_geocoded_at
    await refresh_home_if_stale(db_session, FailingGeocoder(AssertionError()), couple.id)
    assert (float(couple.home_lat), couple.home_place_id, couple.home_geocoded_at) == (
        1.0,
        "old",
        before,
    )


@pytest.mark.parametrize(
    "error", [GeocodeNotFound(), ProviderUnavailable("google_geocode", "down")]
)
async def test_a_failed_refresh_leaves_the_home_area_unchanged(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession, error: Exception
) -> None:
    couple = await stale_home(db_app, db_client, db_session, timedelta(days=45))
    before = couple.home_geocoded_at
    await refresh_home_if_stale(db_session, FailingGeocoder(error), couple.id)
    assert (float(couple.home_lat), couple.home_place_id, couple.home_geocoded_at) == (
        1.0,
        "old",
        before,
    )
    assert couple.home_area_label == "Zilker Park"


async def test_put_home_area_is_503_when_no_geocoder_is_available(db_schema: str) -> None:
    app = create_app(make_settings(database_url=db_schema, demo_mode=False))
    async with browser(app) as client:
        await signed_in(app, client)
        response = await call(client, "PUT", HOME, {"query": "Austin"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "provider_unavailable"


async def test_anonymous_put_home_area_is_401_even_when_the_geocoder_is_unavailable(
    db_schema: str,
) -> None:
    app = create_app(make_settings(database_url=db_schema, demo_mode=False))
    async with browser(app) as client:
        response = await call(client, "PUT", HOME, {"query": "Austin"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"


# -- constraints ------------------------------------------------------------------------------

FULL_CONSTRAINTS: dict[str, Any] = {
    "budget_min": 20,
    "budget_max": 80.5,
    "currency": "EUR",
    "max_travel_minutes": 45,
    "max_radius_km": 25,
    "transport_modes": ["car", "bike", "car"],
    "alcohol": "some",
    "kids_pets_notes": "A sleepy dog",
}


async def test_constraints_start_empty(db_app: FastAPI, db_client: AsyncClient) -> None:
    user = await signed_in(db_app, db_client)
    response = await db_client.get(CONSTRAINTS)
    assert response.status_code == 200
    body = response.json()
    assert body["budget_min"] is None and body["budget_max"] is None
    assert body["currency"] == "USD"
    assert body["transport_modes"] == []
    assert body["alcohol"] is None
    assert body["updated_by_user_id"] is None
    assert user["couple_id"]


async def test_put_constraints_replaces_everything(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await signed_in(db_app, db_client)
    response = await call(db_client, "PUT", CONSTRAINTS, FULL_CONSTRAINTS)
    assert response.status_code == 200
    body = response.json()
    assert body["budget_min"] == 20.0 and body["budget_max"] == 80.5
    assert body["currency"] == "EUR"
    assert body["transport_modes"] == ["car", "bike"]  # deduped, order kept
    assert body["alcohol"] == "some"
    assert body["updated_by_user_id"] == user["id"]
    assert (await db_client.get(CONSTRAINTS)).json() == body

    # A full replace: what is left out goes back to its default.
    again = (await call(db_client, "PUT", CONSTRAINTS, {"budget_max": 10})).json()
    assert again["budget_min"] is None and again["budget_max"] == 10.0
    assert again["currency"] == "USD"
    assert again["transport_modes"] == []
    assert again["kids_pets_notes"] is None
    await db_session.rollback()
    assert (
        await db_session.execute(select(func.count()).select_from(CoupleConstraints))
    ).scalar_one() == 1


@pytest.mark.parametrize(
    "body",
    [
        {"budget_min": 50, "budget_max": 10},
        {"budget_min": -1},
        {"currency": "usd"},
        {"currency": "US"},
        {"transport_modes": ["teleport"]},
        {"alcohol": "lots"},
        {"max_travel_minutes": 0},
        {"kids_pets_notes": "x" * 2001},
    ],
)
async def test_put_constraints_validates(
    db_app: FastAPI, db_client: AsyncClient, body: dict[str, Any]
) -> None:
    await signed_in(db_app, db_client)
    response = await call(db_client, "PUT", CONSTRAINTS, body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_equal_budget_bounds_are_fine(db_app: FastAPI, db_client: AsyncClient) -> None:
    await signed_in(db_app, db_client)
    response = await call(db_client, "PUT", CONSTRAINTS, {"budget_min": 30, "budget_max": 30})
    assert response.status_code == 200


# -- important dates --------------------------------------------------------------------------


async def test_important_dates_crud(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await signed_in(db_app, db_client)
    assert (await db_client.get(DATES)).json() == []
    ids = await profile_ids(db_client)

    later = await call(
        db_client,
        "POST",
        DATES,
        {"name": "Anniversary", "date": "2020-09-12", "recurring": True, "kind": "anniversary"},
    )
    assert later.status_code == 201
    assert later.json()["recurring"] is True
    earlier = await call(
        db_client,
        "POST",
        DATES,
        {
            "name": "Her birthday",
            "date": "1990-03-01",
            "kind": "birthday",
            "profile_id": ids["partner"],
        },
    )
    assert earlier.status_code == 201
    assert earlier.json()["recurring"] is False
    assert earlier.json()["profile_id"] == ids["partner"]

    listed = (await db_client.get(DATES)).json()
    assert [d["name"] for d in listed] == ["Her birthday", "Anniversary"]  # by date

    patched = await call(
        db_client,
        "PATCH",
        f"{DATES}/{earlier.json()['id']}",
        {"name": "Bea's birthday", "recurring": True},
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Bea's birthday"
    assert patched.json()["recurring"] is True
    assert patched.json()["date"] == "1990-03-01"  # untouched
    assert patched.json()["profile_id"] == ids["partner"]
    cleared = await call(
        db_client, "PATCH", f"{DATES}/{earlier.json()['id']}", {"profile_id": None}
    )
    assert cleared.json()["profile_id"] is None

    created_by = (
        (await db_session.execute(select(ImportantDate.created_by_user_id))).scalars().all()
    )
    assert set(created_by) == {uuid.UUID(user["id"])}

    gone = await call(db_client, "DELETE", f"{DATES}/{later.json()['id']}")
    assert gone.status_code == 204
    assert [d["name"] for d in (await db_client.get(DATES)).json()] == ["Bea's birthday"]
    assert (await call(db_client, "DELETE", f"{DATES}/{later.json()['id']}")).status_code == 404


async def test_important_date_patch_nulls(db_app: FastAPI, db_client: AsyncClient) -> None:
    await signed_in(db_app, db_client)
    ids = await profile_ids(db_client)
    created = await call(
        db_client,
        "POST",
        DATES,
        {"name": "Hers", "date": "1990-03-01", "kind": "birthday", "profile_id": ids["partner"]},
    )
    url = f"{DATES}/{created.json()['id']}"
    for field in ("name", "date", "recurring", "kind"):
        assert (await call(db_client, "PATCH", url, {field: None})).status_code == 422, field
    omitted = await call(db_client, "PATCH", url, {"recurring": True})
    assert omitted.json()["name"] == "Hers" and omitted.json()["profile_id"] == ids["partner"]
    assert (await call(db_client, "PATCH", url, {"profile_id": None})).json()["profile_id"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "date": "2020-01-01", "kind": "custom"},
        {"name": "x", "date": "not-a-date", "kind": "custom"},
        {"name": "x", "date": "2020-01-01", "kind": "party"},
        {"name": "x" * 121, "date": "2020-01-01", "kind": "custom"},
    ],
)
async def test_important_date_validates(
    db_app: FastAPI, db_client: AsyncClient, body: dict[str, Any]
) -> None:
    await signed_in(db_app, db_client)
    assert (await call(db_client, "POST", DATES, body)).status_code == 422


async def test_important_date_profile_must_be_in_the_couple(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await signed_in(db_app, db_client)
    async with browser(db_app) as bob:
        await sign_in(db_app, bob, "bob@example.com")
        bobs_profile = (await profile_ids(bob))["owner"]
    body = {"name": "x", "date": "2020-01-01", "kind": "birthday"}
    for profile_id in (bobs_profile, str(uuid.uuid4())):
        response = await call(db_client, "POST", DATES, {**body, "profile_id": profile_id})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_profile"
    ok = await call(db_client, "POST", DATES, body)
    patch = await call(
        db_client, "PATCH", f"{DATES}/{ok.json()['id']}", {"profile_id": bobs_profile}
    )
    assert patch.status_code == 422
    assert patch.json()["error"]["code"] == "invalid_profile"
    assert (await db_client.get(DATES)).json()[0]["profile_id"] is None


async def test_both_members_share_the_dates_and_constraints(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        created = await call(
            owner, "POST", DATES, {"name": "Trip", "date": "2030-05-05", "kind": "custom"}
        )
        assert [d["id"] for d in (await partner.get(DATES)).json()] == [created.json()["id"]]
        await call(partner, "PUT", CONSTRAINTS, {"alcohol": "none"})
        assert (await owner.get(CONSTRAINTS)).json()["alcohol"] == "none"
        partner_id = (await partner.get("/api/v1/me")).json()["id"]
        assert (await owner.get(CONSTRAINTS)).json()["updated_by_user_id"] == partner_id

"""Who sees and edits an itinerary: the access helper on its own."""

import uuid

import pytest
from fastapi import FastAPI

from app.errors import ApiError
from app.services.itinerary_access import get_editable, get_visible, visible_itineraries
from tests.authutil import browser, sign_in
from tests.itinerary_helpers import World, seed, when, world

pytestmark = pytest.mark.db


async def seed_all(w: World) -> dict[str, uuid.UUID]:
    """One itinerary of each visibility kind, all planned by the owner."""
    announced = {"status": "scheduled", "starts_at": when(), "announced_at": when(-1)}
    return {
        "draft": await seed(w, title="draft"),
        "announced": await seed(w, title="announced", **announced),
        "surprise": await seed(w, title="surprise", is_surprise=True, **announced),
        "scheduled_unannounced": await seed(w, title="quiet", status="scheduled", starts_at=when()),
        "cancelled": await seed(w, title="cancelled", **{**announced, "status": "cancelled"}),
    }


async def titles(w: World, viewer: uuid.UUID) -> set[str]:
    async with w.app.state.sessionmaker() as session:
        rows = await session.execute(visible_itineraries(session, viewer, w.couple_id))
        return {i.title for i in rows.scalars()}


async def test_planner_sees_everything_the_partner_sees_only_announced(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await seed_all(w)
        assert await titles(w, w.owner_id) == {
            "draft",
            "announced",
            "surprise",
            "quiet",
            "cancelled",
        }
        assert await titles(w, w.partner_id) == {"announced", "cancelled"}


async def test_get_visible_is_404_for_a_hidden_itinerary(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        ids = await seed_all(w)
        async with db_app.state.sessionmaker() as session:
            for name in ("draft", "surprise", "scheduled_unannounced"):
                with pytest.raises(ApiError) as hidden:
                    await get_visible(session, w.partner_id, w.couple_id, ids[name])
                assert (hidden.value.status, hidden.value.code) == (404, "not_found")
                assert (await get_visible(session, w.owner_id, w.couple_id, ids[name])).id == ids[
                    name
                ]
            seen = await get_visible(session, w.partner_id, w.couple_id, ids["announced"])
            assert seen.id == ids["announced"]
            with pytest.raises(ApiError) as missing:
                await get_visible(session, w.owner_id, w.couple_id, uuid.uuid4())
            assert missing.value.status == 404


async def test_another_couples_itinerary_is_never_visible(db_app: FastAPI) -> None:
    async with world(db_app) as w, browser(db_app) as stranger:
        await sign_in(db_app, stranger, "zed@example.com")
        other = (await stranger.get("/api/v1/me")).json()
        other_couple = uuid.UUID(other["couple_id"])
        itinerary_id = await seed(
            w, couple_id=other_couple, status="scheduled", announced_at=when(-1), starts_at=when()
        )
        async with db_app.state.sessionmaker() as session:
            for viewer in (w.owner_id, w.partner_id):
                with pytest.raises(ApiError) as exc:
                    await get_visible(session, viewer, w.couple_id, itinerary_id)
                assert exc.value.status == 404


async def test_get_editable_is_403_for_a_visible_non_planner_and_404_when_hidden(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        ids = await seed_all(w)
        async with db_app.state.sessionmaker() as session:
            planned = await get_editable(session, w.owner_id, w.couple_id, ids["announced"])
            assert planned.id == ids["announced"]
            with pytest.raises(ApiError) as forbidden:
                await get_editable(session, w.partner_id, w.couple_id, ids["announced"])
            assert (forbidden.value.status, forbidden.value.code) == (403, "forbidden")
            for name in ("draft", "surprise"):
                with pytest.raises(ApiError) as hidden:
                    await get_editable(session, w.partner_id, w.couple_id, ids[name])
                assert hidden.value.status == 404

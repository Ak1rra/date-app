"""The itinerary API: visibility, live venue enrichment, stop edits, checklist, schedule, react,
and venue search."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.config import Settings
from app.errors import ApiError
from app.models import ChecklistItem, Itinerary, Stop, TravelLeg
from app.providers.places.fake import FakePlaces
from app.providers.registry import get_places
from app.providers.routes.fake import FakeRoutes
from app.providers.types import LatLng
from app.services import itineraries as service
from app.services.itineraries import maps_links
from tests.authutil import call
from tests.itinerary_helpers import (
    ALLOWED_VENUE_KEYS,
    GELATO,
    ITINERARIES,
    TAQUERIA,
    TRATTORIA,
    CountingPlaces,
    CountingRoutes,
    DownPlaces,
    DownRoutes,
    World,
    fake_providers,
    seed,
    set_home,
    when,
    world,
)

pytestmark = pytest.mark.db


async def snapshot(place_id: str, age_days: int = 0) -> dict[str, Any]:
    place = await FakePlaces().details(place_id)
    assert place.location is not None
    fetched = dt.datetime.now(dt.UTC) - dt.timedelta(days=age_days)
    return {"lat": place.location.lat, "lng": place.location.lng, "fetched_at": fetched.isoformat()}


async def three_stops(w: World, **fields: Any) -> uuid.UUID:
    """A scheduled, announced date with three stops at known demo venues."""
    stops = []
    for index, place_id in enumerate((TRATTORIA, TAQUERIA, GELATO)):
        stops.append(
            {
                "title": f"Stop {index}",
                "place_id": place_id,
                "venue": await snapshot(place_id),
                "starts_at": when(hour=17 + index),
                "ends_at": when(hour=18 + index),
            }
        )
    values: dict[str, Any] = {
        "status": "scheduled",
        "announced_at": when(-1),
        "starts_at": when(hour=17),
    }
    values.update(fields)
    return await seed(w, stops=stops, **values)


async def db_stops(w: World, itinerary_id: uuid.UUID) -> list[Stop]:
    async with w.app.state.sessionmaker() as session:
        rows = await session.execute(
            select(Stop).where(Stop.itinerary_id == itinerary_id).order_by(Stop.position)
        )
        return list(rows.scalars())


async def db_itinerary(w: World, itinerary_id: uuid.UUID) -> Itinerary:
    async with w.app.state.sessionmaker() as session:
        return (
            await session.execute(select(Itinerary).where(Itinerary.id == itinerary_id))
        ).scalar_one()


async def db_legs(w: World, itinerary_id: uuid.UUID) -> list[TravelLeg]:
    async with w.app.state.sessionmaker() as session:
        rows = await session.execute(
            select(TravelLeg).where(TravelLeg.itinerary_id == itinerary_id)
        )
        return list(rows.scalars())


def stop_body(stop: dict[str, Any], **changes: Any) -> dict[str, Any]:
    body = {
        "id": stop["id"],
        "title": stop["title"],
        "place_id": stop["place_id"],
        "starts_at": stop["starts_at"],
        "ends_at": stop["ends_at"],
    }
    body.update(changes)
    return body


def assert_venue_snapshots_only(stops: list[Stop]) -> None:
    for stop in stops:
        if stop.venue is not None:
            assert set(stop.venue) <= ALLOWED_VENUE_KEYS, stop.venue


# --- Visibility


async def test_list_shows_the_partner_only_announced_dates(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        announced = await seed(
            w, title="Announced", status="scheduled", announced_at=when(-1), starts_at=when(5)
        )
        draft = await seed(w, title="Draft")
        surprise = await seed(
            w,
            title="Surprise",
            status="scheduled",
            announced_at=when(-1),
            is_surprise=True,
            starts_at=when(6),
        )
        await seed(
            w,
            title="Theirs",
            planner="partner",
            status="scheduled",
            announced_at=when(-1),
            starts_at=when(7),
        )

        mine = (await w.owner.get(ITINERARIES)).json()
        assert {i["title"] for i in mine} == {"Announced", "Draft", "Surprise", "Theirs"}
        by_title = {i["title"]: i for i in mine}
        assert by_title["Surprise"]["is_surprise"] is True
        assert by_title["Announced"]["is_planner"] is True
        assert by_title["Theirs"]["is_planner"] is False
        assert by_title["Draft"]["announced"] is False
        assert by_title["Announced"]["announced"] is True

        theirs = (await w.partner.get(ITINERARIES)).json()
        assert {i["title"] for i in theirs} == {"Announced", "Theirs"}

        assert (await w.partner.get(f"{ITINERARIES}/{announced}")).status_code == 200
        for hidden in (draft, surprise):
            response = await w.partner.get(f"{ITINERARIES}/{hidden}")
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"
        assert (await w.owner.get(f"{ITINERARIES}/{uuid.uuid4()}")).status_code == 404


async def test_list_filters_and_counts_stops(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        soon = await seed(
            w,
            title="Soon",
            status="scheduled",
            starts_at=when(3),
            stops=[
                {"title": "A", "starts_at": when(3), "ends_at": when(3, 19)},
                {"title": "B", "starts_at": when(3, 19), "ends_at": when(3, 20)},
            ],
        )
        await seed(w, title="Later", status="scheduled", starts_at=when(30))
        await seed(w, title="Undated")

        listed = (await w.owner.get(ITINERARIES)).json()
        assert [i["title"] for i in listed] == ["Soon", "Later", "Undated"]  # nulls last
        assert {i["id"]: i["stop_count"] for i in listed}[str(soon)] == 2

        scheduled = (await w.owner.get(ITINERARIES, params={"status": "scheduled"})).json()
        assert [i["title"] for i in scheduled] == ["Soon", "Later"]
        window = (
            await w.owner.get(
                ITINERARIES,
                params={"from": when(1).isoformat(), "to": when(10).isoformat()},
            )
        ).json()
        assert [i["title"] for i in window] == ["Soon"]
        assert (await w.owner.get(ITINERARIES, params={"status": "bogus"})).status_code == 422


async def test_partner_opening_an_announced_date_marks_it_seen(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w, status="scheduled", announced_at=when(-1), starts_at=when(), title="Seen?"
        )
        assert (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).status_code == 200
        assert (await db_itinerary(w, itinerary_id)).seen_by_partner_at is None

        body = (await w.partner.get(f"{ITINERARIES}/{itinerary_id}")).json()
        assert body["is_planner"] is False
        first_seen = (await db_itinerary(w, itinerary_id)).seen_by_partner_at
        assert first_seen is not None
        await w.partner.get(f"{ITINERARIES}/{itinerary_id}")
        assert (await db_itinerary(w, itinerary_id)).seen_by_partner_at == first_seen


async def test_partner_gets_403_on_visible_edits_and_404_on_hidden_ones(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        visible = await seed(w, status="scheduled", announced_at=when(-1), starts_at=when())
        hidden = await seed(w)
        one_stop = {"title": "x", "starts_at": when().isoformat(), "ends_at": when().isoformat()}
        calls: list[tuple[str, str, Any]] = [
            ("PATCH", "", {"title": "New"}),
            ("PUT", "/stops", {"stops": [one_stop]}),
            ("POST", "/checklist", {"text": "Book", "kind": "reserve"}),
            ("PATCH", f"/checklist/{uuid.uuid4()}", {"done": True}),
            ("DELETE", f"/checklist/{uuid.uuid4()}", None),
            ("POST", "/schedule", {"starts_at": when().isoformat(), "mode": "announce"}),
            ("POST", "/cancel", None),
        ]
        for method, suffix, body in calls:
            response = await call(w.partner, method, f"{ITINERARIES}/{visible}{suffix}", body)
            assert response.status_code == 403, (method, suffix, response.text)
            assert response.json()["error"]["code"] == "forbidden"
            response = await call(w.partner, method, f"{ITINERARIES}/{hidden}{suffix}", body)
            assert response.status_code == 404, (method, suffix, response.text)


# --- Live venue enrichment


async def test_venue_is_fetched_live_and_never_stored(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        places = CountingPlaces()
        w.providers["p"] = fake_providers(places=places)
        itinerary_id = await three_stops(w)

        body = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()
        first = body["stops"][0]
        assert first["venue"]["name"] == "La Trattoria"
        assert first["venue"]["address"].endswith("Austin, TX")
        assert first["venue"]["rating"] == 4.6
        assert first["venue"]["website"] == "https://example.com/la-trattoria"
        assert first["venue"]["maps_uri"].startswith("https://example.com/maps/")
        assert first["venue"]["lat"] is not None and first["venue"]["lng"] is not None
        assert first["booking_url"] == "https://example.com/la-trattoria"
        assert places.details_calls == [TRATTORIA, TAQUERIA, GELATO]
        assert "Demo data (not real venues)" in body["attributions"]

        stored = await db_stops(w, itinerary_id)
        assert_venue_snapshots_only(stored)
        assert all(s.venue is not None and set(s.venue) == ALLOWED_VENUE_KEYS for s in stored)
        assert all(s.booking_url is None for s in stored)  # never written from provider data


async def test_event_stop_uses_its_event_url_for_booking_and_credits_ticketmaster(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            stops=[
                {
                    "title": "Concert",
                    "event": {"id": "ev1", "name": "Concert", "url": "https://tickets.example/ev1"},
                    "venue": await snapshot(TRATTORIA),
                    "starts_at": when(),
                    "ends_at": when(hour=21),
                }
            ],
            weather={"summary": "Clear", "attribution": "Weather data by Open-Meteo.com"},
        )
        body = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()
        assert body["stops"][0]["booking_url"] == "https://tickets.example/ev1"
        assert body["stops"][0]["venue"]["name"] is None  # no place id, so no live name
        assert body["attributions"] == [
            "Weather data by Open-Meteo.com",
            "Events from Ticketmaster",
        ]


@pytest.mark.parametrize("down", ["places_down", "places_missing"])
async def test_provider_down_degrades_to_stored_coordinates(db_app: FastAPI, down: str) -> None:
    async with world(db_app) as w:
        w.providers["p"] = fake_providers(places=DownPlaces() if down == "places_down" else None)
        itinerary_id = await three_stops(w)
        stored_before = [s.venue for s in await db_stops(w, itinerary_id)]

        response = await w.owner.get(f"{ITINERARIES}/{itinerary_id}")
        assert response.status_code == 200
        stop = response.json()["stops"][0]
        assert stop["venue"]["name"] is None and stop["venue"]["rating"] is None
        assert stop["venue"]["lat"] == stored_before[0]["lat"]
        assert stop["venue"]["lng"] == stored_before[0]["lng"]
        assert stop["booking_url"] is None
        assert stop["maps"]["google"] is not None  # coordinates are enough for a link
        assert [s.venue for s in await db_stops(w, itinerary_id)] == stored_before


async def test_stale_snapshot_is_refreshed_and_fresh_one_is_left_alone(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        live = await snapshot(TRATTORIA)
        stale = {"lat": 1.0, "lng": 2.0, "fetched_at": (when(-40)).isoformat()}
        fresh = {"lat": 3.0, "lng": 4.0, "fetched_at": (when(-1)).isoformat()}
        itinerary_id = await seed(
            w,
            stops=[
                {"title": "Stale", "place_id": TRATTORIA, "venue": stale},
                {"title": "Fresh", "place_id": TAQUERIA, "venue": fresh},
                {"title": "Bare", "place_id": GELATO},
            ],
        )

        body = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()
        assert body["stops"][0]["venue"]["lat"] == live["lat"]  # the response is live data

        stale_row, fresh_row, bare_row = await db_stops(w, itinerary_id)
        assert stale_row.venue is not None
        assert stale_row.venue["lat"] == live["lat"] and stale_row.venue["lng"] == live["lng"]
        assert dt.datetime.fromisoformat(stale_row.venue["fetched_at"]) > when(-1)
        assert fresh_row.venue == fresh
        assert bare_row.venue is not None  # a missing snapshot is filled in too
        assert_venue_snapshots_only([stale_row, fresh_row, bare_row])

        # A failed lookup never touches the stored snapshot, however old.
        w.providers["p"] = fake_providers(places=DownPlaces())
        other = await seed(w, stops=[{"title": "Stale", "place_id": TRATTORIA, "venue": stale}])
        assert (await w.owner.get(f"{ITINERARIES}/{other}")).status_code == 200
        assert (await db_stops(w, other))[0].venue == stale


def test_maps_link_formats() -> None:
    here, there = LatLng(30.25, -97.75), LatLng(30.3, -97.7)
    first = maps_links(
        title="Dinner",
        name="La Trattoria & Bar",
        coords=here,
        place_id="pid1",
        previous=None,
        mode=None,
    )
    assert first.google == (
        "https://www.google.com/maps/search/?api=1&query=La%20Trattoria%20%26%20Bar"
        "&query_place_id=pid1"
    )
    assert first.apple == (
        "https://maps.apple.com/place?coordinate=30.25,-97.75&name=La%20Trattoria%20%26%20Bar"
    )
    assert first.directions_from_previous is None

    second = maps_links(
        title="Walk",
        name=None,
        coords=there,
        place_id=None,
        previous=(here, "pid1"),
        mode="walk",
    )
    assert second.google == "https://www.google.com/maps/search/?api=1&query=30.3,-97.7"
    assert second.apple == "https://maps.apple.com/place?coordinate=30.3,-97.7&name=Walk"
    assert second.directions_from_previous == (
        "https://www.google.com/maps/dir/?api=1&origin=30.25,-97.75&origin_place_id=pid1"
        "&destination=30.3,-97.7&travelmode=walking"
    )
    for mode, expected in (("drive", "driving"), ("bicycle", "bicycling"), ("transit", "transit")):
        link = maps_links(
            title="x", name=None, coords=there, place_id="p2", previous=(here, None), mode=mode
        )
        assert link.directions_from_previous == (
            "https://www.google.com/maps/dir/?api=1&origin=30.25,-97.75"
            f"&destination=30.3,-97.7&destination_place_id=p2&travelmode={expected}"
        )

    none = maps_links(
        title="x", name="y", coords=None, place_id="p", previous=(here, None), mode="drive"
    )
    assert none.model_dump() == {"apple": None, "google": None, "directions_from_previous": None}


async def test_maps_links_and_travel_appear_on_stops(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        w.providers["p"] = fake_providers(routes=DownRoutes())
        itinerary_id = await three_stops(w)
        async with w.app.state.sessionmaker() as session:
            stops = await db_stops(w, itinerary_id)
            session.add(
                TravelLeg(
                    itinerary_id=itinerary_id,
                    from_stop_id=stops[0].id,
                    to_stop_id=stops[1].id,
                    mode="walk",
                    duration_minutes=12,
                    distance_m=900,
                )
            )
            await session.commit()
        body = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()
        first, second, third = body["stops"]
        assert first["travel_to_next"] == {
            "mode": "walk",
            "duration_minutes": 12,
            "distance_m": 900,
            "source": "estimate",
        }
        assert second["travel_to_next"] is None
        assert first["maps"]["directions_from_previous"] is None
        assert first["maps"]["google"].endswith(f"query=La%20Trattoria&query_place_id={TRATTORIA}")
        assert second["maps"]["directions_from_previous"].endswith(
            f"&destination_place_id={TAQUERIA}&travelmode=walking"
        )
        assert f"origin_place_id={TRATTORIA}" in second["maps"]["directions_from_previous"]
        assert third["maps"]["directions_from_previous"].endswith("&travelmode=driving")
        assert first["maps"]["apple"].startswith("https://maps.apple.com/place?coordinate=")


# --- PUT /stops


async def test_replace_stops_reorders_removes_adds_and_recomputes_legs(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await three_stops(w)
        a, b, c = await db_stops(w, itinerary_id)
        async with w.app.state.sessionmaker() as session:
            session.add(
                TravelLeg(
                    itinerary_id=itinerary_id,
                    from_stop_id=a.id,
                    to_stop_id=b.id,
                    mode="transit",
                    duration_minutes=999,
                    distance_m=1,
                )
            )
            await session.commit()
        current = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        by_id = {s["id"]: s for s in current}

        new_stop = {
            "title": "Dessert",
            "place_id": "demo-place-sweet-spot-bakery",
            "starts_at": when(hour=21).isoformat(),
            "ends_at": when(hour=22).isoformat(),
            "est_cost": 12.5,
            "notes": "ask for the window seat",
            "reveal_policy": "at_stop_time",
        }
        body = {
            "stops": [
                stop_body(by_id[str(c.id)]),
                stop_body(by_id[str(a.id)], title="Dinner (renamed)"),
                new_stop,
            ]
        }
        response = await call(w.owner, "PUT", f"{ITINERARIES}/{itinerary_id}/stops", body)
        assert response.status_code == 200, response.text
        out = response.json()
        stops = out["stops"]
        assert [s["position"] for s in stops] == [0, 1, 2]
        assert [s["title"] for s in stops] == ["Stop 2", "Dinner (renamed)", "Dessert"]
        assert stops[0]["id"] == str(c.id) and stops[1]["id"] == str(a.id)  # ids kept
        assert stops[2]["id"] not in {str(a.id), str(b.id), str(c.id)}
        assert stops[2]["reveal_policy"] == "at_stop_time"
        assert stops[2]["venue"]["name"] == "Sweet Spot Bakery"
        assert out["est_total"] == 12.5

        rows = await db_stops(w, itinerary_id)
        assert [r.title for r in rows] == ["Stop 2", "Dinner (renamed)", "Dessert"]
        assert b.id not in {r.id for r in rows}
        assert rows[2].venue is not None and set(rows[2].venue) == ALLOWED_VENUE_KEYS
        assert_venue_snapshots_only(rows)

        legs = await db_legs(w, itinerary_id)
        pairs = {(leg.from_stop_id, leg.to_stop_id): leg for leg in legs}
        assert set(pairs) == {(rows[0].id, rows[1].id), (rows[1].id, rows[2].id)}
        assert all(leg.duration_minutes != 999 for leg in legs)
        # The mode of the leg that left stop a carries over; a stop with no old leg gets the
        # first existing leg's mode.
        assert pairs[(rows[1].id, rows[2].id)].mode == "transit"
        assert pairs[(rows[0].id, rows[1].id)].mode == "transit"
        assert stops[0]["travel_to_next"]["mode"] == "transit"
        assert stops[2]["travel_to_next"] is None


async def test_replace_stops_validates_ids_and_shape(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await three_stops(w)
        other = await seed(w, stops=[{"title": "Elsewhere"}])
        foreign = (await db_stops(w, other))[0]
        mine = (await db_stops(w, itinerary_id))[0]
        url = f"{ITINERARIES}/{itinerary_id}/stops"

        def item(**extra: Any) -> dict[str, Any]:
            return {
                "title": "X",
                "starts_at": when().isoformat(),
                "ends_at": when(hour=19).isoformat(),
                **extra,
            }

        bad_bodies = [
            {"stops": [item(id=str(foreign.id))]},  # another itinerary's stop
            {"stops": [item(id=str(uuid.uuid4()))]},  # unknown id
            {"stops": [item(id=str(mine.id)), item(id=str(mine.id))]},  # twice
        ]
        for body in bad_bodies:
            response = await call(w.owner, "PUT", url, body)
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "invalid_stops"
        too_many = await call(w.owner, "PUT", url, {"stops": [item() for _ in range(9)]})
        assert too_many.status_code == 422
        backwards = await call(
            w.owner,
            "PUT",
            url,
            {
                "stops": [
                    item(starts_at=when(hour=20).isoformat(), ends_at=when(hour=19).isoformat())
                ]
            },
        )
        assert backwards.status_code == 422
        assert (await call(w.owner, "PUT", url, {"stops": []})).status_code == 422
        # Nothing was changed by any of those.
        assert len(await db_stops(w, itinerary_id)) == 3
        assert [s.title for s in await db_stops(w, other)] == ["Elsewhere"]


async def test_replace_stops_with_places_down_is_503_and_changes_nothing(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await three_stops(w)
        before = await db_stops(w, itinerary_id)
        w.providers["p"] = fake_providers(places=DownPlaces())
        body = {
            "stops": [
                {
                    "title": "New",
                    "place_id": GELATO,
                    "starts_at": when().isoformat(),
                    "ends_at": when(hour=19).isoformat(),
                }
            ]
        }
        response = await call(w.owner, "PUT", f"{ITINERARIES}/{itinerary_id}/stops", body)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "provider_unavailable"
        after = await db_stops(w, itinerary_id)
        assert [(s.id, s.title) for s in after] == [(s.id, s.title) for s in before]

        w.providers["p"] = fake_providers(places=None)
        response = await call(w.owner, "PUT", f"{ITINERARIES}/{itinerary_id}/stops", body)
        assert response.status_code == 503


async def test_replace_stops_keeps_known_places_without_a_lookup_and_survives_routes_down(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await three_stops(w)
        current = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        places = CountingPlaces()
        w.providers["p"] = fake_providers(places=places, routes=DownRoutes())
        before = [s.venue for s in await db_stops(w, itinerary_id)]
        body = {"stops": [stop_body(s, notes="n") for s in current]}
        response = await call(w.owner, "PUT", f"{ITINERARIES}/{itinerary_id}/stops", body)
        assert response.status_code == 200, response.text
        # Only itinerary_out looked places up (3 stops); the edit itself reused the snapshots.
        assert places.details_calls == [TRATTORIA, TAQUERIA, GELATO]
        assert [s.venue for s in await db_stops(w, itinerary_id)] == before
        stops = response.json()["stops"]
        # Routes down: the stored estimates are served, marked as estimates.
        assert [s["travel_to_next"]["source"] for s in stops[:2]] == ["estimate", "estimate"]
        assert stops[2]["travel_to_next"] is None
        assert len(await db_legs(w, itinerary_id)) == 2


def _coords(stop: Stop) -> LatLng:
    assert stop.venue is not None
    return LatLng(stop.venue["lat"], stop.venue["lng"])


async def test_stored_legs_are_our_estimate_and_routes_are_not_called_on_write(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await three_stops(w)
        routes = CountingRoutes()
        w.providers["p"] = fake_providers(routes=routes)
        current = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        response = await call(
            w.owner,
            "PUT",
            f"{ITINERARIES}/{itinerary_id}/stops",
            {"stops": [stop_body(s) for s in current]},
        )
        assert response.status_code == 200, response.text
        # The legs did not exist before the write, so these two calls are the response's live read.
        assert len(routes.calls) == 2
        rows = await db_stops(w, itinerary_id)
        legs = {leg.from_stop_id: leg for leg in await db_legs(w, itinerary_id)}
        assert len(legs) == 2
        for first, second in zip(rows, rows[1:], strict=False):
            leg = legs[first.id]
            assert (leg.duration_minutes, leg.distance_m) == service.estimate_leg(
                _coords(first), _coords(second), leg.mode
            )


async def test_recompute_legs_never_calls_routes_at_write_time(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await three_stops(w)
        routes = CountingRoutes()
        w.providers["p"] = fake_providers(routes=routes)
        async with w.app.state.sessionmaker() as session:
            itinerary = await session.get(Itinerary, itinerary_id)
            assert itinerary is not None
            await service.recompute_legs(
                session, itinerary, await service.load_stops(session, itinerary_id)
            )
            await session.commit()
        assert routes.calls == []
        assert len(await db_legs(w, itinerary_id)) == 2


async def test_get_serves_live_legs_or_the_estimate_and_never_writes_them(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await three_stops(w)
        rows = await db_stops(w, itinerary_id)
        async with w.app.state.sessionmaker() as session:
            session.add(
                TravelLeg(
                    itinerary_id=itinerary_id,
                    from_stop_id=rows[0].id,
                    to_stop_id=rows[1].id,
                    mode="walk",
                    duration_minutes=1,
                    distance_m=2,
                )
            )
            await session.commit()
        routes = CountingRoutes()
        w.providers["p"] = fake_providers(routes=routes)
        live = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"][0]
        expected = await FakeRoutes().travel(_coords(rows[0]), _coords(rows[1]), "walk", when())
        assert len(routes.calls) == 1
        assert live["travel_to_next"] == {
            "mode": "walk",
            "duration_minutes": expected.duration_minutes,
            "distance_m": expected.distance_m,
            "source": "live",
        }

        w.providers["p"] = fake_providers(routes=DownRoutes())
        fallback = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"][0]
        assert fallback["travel_to_next"] == {
            "mode": "walk",
            "duration_minutes": 1,
            "distance_m": 2,
            "source": "estimate",
        }
        legs = await db_legs(w, itinerary_id)
        assert [(leg.duration_minutes, leg.distance_m) for leg in legs] == [(1, 2)]


async def test_snapshot_age_is_capped_at_30_days_whatever_the_setting(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        stale = await snapshot(TRATTORIA, age_days=31)
        stale["lat"] += 1.0
        itinerary_id = await seed(
            w, stops=[{"title": "Old", "place_id": TRATTORIA, "venue": stale}]
        )
        settings: Settings = w.app.state.settings.model_copy(
            update={"venue_snapshot_max_age_days": 90}
        )
        async with w.app.state.sessionmaker() as session:
            itinerary = await session.get(Itinerary, itinerary_id)
            assert itinerary is not None
            await service.itinerary_out(
                session, itinerary, w.owner_id, w.providers["p"], settings=settings
            )
            await session.commit()
        row = (await db_stops(w, itinerary_id))[0]
        assert row.venue is not None
        assert row.venue["lat"] == (await snapshot(TRATTORIA))["lat"]  # refreshed from live


async def test_replace_stops_rewrites_a_stored_venue_to_exactly_lat_lng_fetched_at(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        legacy = await snapshot(TRATTORIA)
        legacy["name"] = "La Trattoria"
        legacy["rating"] = 4.6
        itinerary_id = await seed(
            w,
            stops=[
                {
                    "title": "Dinner",
                    "place_id": TRATTORIA,
                    "venue": legacy,
                    "starts_at": when(),
                    "ends_at": when(hour=19),
                }
            ],
        )
        current = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()["stops"]
        response = await call(
            w.owner,
            "PUT",
            f"{ITINERARIES}/{itinerary_id}/stops",
            {"stops": [stop_body(s) for s in current]},
        )
        assert response.status_code == 200, response.text
        row = (await db_stops(w, itinerary_id))[0]
        assert row.venue == {
            "lat": legacy["lat"],
            "lng": legacy["lng"],
            "fetched_at": legacy["fetched_at"],
        }


# --- PATCH


async def test_patch_updates_fields_and_a_scheduled_time_change_revalidates(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        await set_home(w)
        itinerary_id = await three_stops(w)
        url = f"{ITINERARIES}/{itinerary_id}"

        response = await call(
            w.owner,
            "PATCH",
            url,
            {"title": "  Our night  ", "budget": 150.5, "backup_plan": "Movie at home"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["title"] == "Our night" and body["budget"] == 150.5
        assert body["backup_plan"] == "Movie at home"
        assert body["weather"] is None  # no time change: no revalidation
        assert await db_legs(w, itinerary_id) == []

        new_start = when(12, 18)
        response = await call(
            w.owner,
            "PATCH",
            url,
            {
                "starts_at": new_start.isoformat(),
                "ends_at": (new_start + dt.timedelta(hours=3)).isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["weather"]["summary"]
        assert "Weather data by Open-Meteo.com" in body["attributions"]
        assert [leg.mode for leg in await db_legs(w, itinerary_id)] == ["drive", "drive"]
        assert body["title"] == "Our night"  # untouched fields stay

        cleared = await call(w.owner, "PATCH", url, {"backup_plan": None, "budget": None})
        assert cleared.json()["backup_plan"] is None and cleared.json()["budget"] is None


async def test_patch_time_change_on_a_draft_does_not_revalidate(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        itinerary_id = await seed(w, starts_at=when(), stops=[])
        response = await call(
            w.owner, "PATCH", f"{ITINERARIES}/{itinerary_id}", {"starts_at": when(3).isoformat()}
        )
        assert response.status_code == 200
        assert response.json()["weather"] is None


async def test_patch_validates(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w, starts_at=when(), ends_at=when(hour=21))
        url = f"{ITINERARIES}/{itinerary_id}"
        for body in (
            {"title": ""},
            {"title": None},
            {"budget": -1},
            {"starts_at": "2026-01-01T10:00:00"},  # no timezone
            {"starts_at": when(hour=22).isoformat()},  # after the stored end
        ):
            assert (await call(w.owner, "PATCH", url, body)).status_code == 422, body


# --- Checklist


async def test_checklist_crud(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        url = f"{ITINERARIES}/{itinerary_id}/checklist"
        due = when(8).isoformat()
        first = await call(
            w.owner,
            "POST",
            url,
            {
                "text": " Reserve a table ",
                "kind": "reserve",
                "due_at": due,
                "link": "https://r.example/x",
            },
        )
        assert first.status_code == 201, first.text
        a = first.json()
        assert a["text"] == "Reserve a table" and a["position"] == 0 and a["done"] is False
        assert a["link"] == "https://r.example/x" and a["done_at"] is None
        b = (await call(w.owner, "POST", url, {"text": "Buy flowers", "kind": "buy"})).json()
        assert b["position"] == 1

        body = (await w.owner.get(f"{ITINERARIES}/{itinerary_id}")).json()
        assert [c["text"] for c in body["checklist"]] == ["Reserve a table", "Buy flowers"]

        done = await call(w.owner, "PATCH", f"{url}/{a['id']}", {"done": True})
        assert done.status_code == 200 and done.json()["done"] is True
        assert done.json()["done_at"] is not None
        edited = await call(
            w.owner,
            "PATCH",
            f"{url}/{a['id']}",
            {"text": "Reserve for 7:30", "kind": "arrange", "due_at": None, "link": None},
        )
        assert edited.json()["text"] == "Reserve for 7:30" and edited.json()["kind"] == "arrange"
        assert edited.json()["due_at"] is None and edited.json()["link"] is None
        assert edited.json()["done"] is True  # untouched
        undone = await call(w.owner, "PATCH", f"{url}/{a['id']}", {"done": False})
        assert undone.json()["done"] is False and undone.json()["done_at"] is None

        assert (await call(w.owner, "DELETE", f"{url}/{b['id']}")).status_code == 204
        assert (await call(w.owner, "DELETE", f"{url}/{b['id']}")).status_code == 404
        assert (
            await call(w.owner, "PATCH", f"{url}/{uuid.uuid4()}", {"done": True})
        ).status_code == 404
        async with w.app.state.sessionmaker() as session:
            rows = (await session.execute(select(ChecklistItem))).scalars().all()
            assert [r.id for r in rows] == [uuid.UUID(a["id"])]


@pytest.mark.parametrize(
    "body",
    [
        {"text": "", "kind": "buy"},
        {"text": "x" * 301, "kind": "buy"},
        {"text": "ok", "kind": "book"},
        {"text": "ok", "kind": "prepare"},
        {"text": "ok"},
        {"text": "ok", "kind": "buy", "link": "ftp://example.com/x"},
        {"text": "ok", "kind": "buy", "link": "javascript:alert(1)"},
        {"text": "ok", "kind": "buy", "link": "not a url"},
        {"text": "ok", "kind": "buy", "due_at": "2026-01-01T10:00:00"},
    ],
)
async def test_checklist_validates(db_app: FastAPI, body: dict[str, Any]) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        response = await call(w.owner, "POST", f"{ITINERARIES}/{itinerary_id}/checklist", body)
        assert response.status_code == 422


async def test_checklist_item_of_another_itinerary_is_404(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        one, two = await seed(w), await seed(w)
        item = (
            await call(
                w.owner, "POST", f"{ITINERARIES}/{one}/checklist", {"text": "a", "kind": "buy"}
            )
        ).json()
        response = await call(
            w.owner, "PATCH", f"{ITINERARIES}/{two}/checklist/{item['id']}", {"done": True}
        )
        assert response.status_code == 404


# --- Schedule, cancel, react


async def test_schedule_announce_makes_the_date_visible_and_runs_the_hook(
    db_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with world(db_app) as w:
        hooked: list[uuid.UUID] = []

        async def on_scheduled(
            session: Any, itinerary: Itinerary, settings: Any, *args: Any
        ) -> None:
            hooked.append(itinerary.id)

        monkeypatch.setattr(service, "on_scheduled", on_scheduled)
        await set_home(w)
        itinerary_id = await three_stops(w, status="draft", announced_at=None, starts_at=None)
        assert (await w.partner.get(f"{ITINERARIES}/{itinerary_id}")).status_code == 404

        start = when(14, 18)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {
                "starts_at": start.isoformat(),
                "ends_at": (start + dt.timedelta(hours=4)).isoformat(),
                "mode": "announce",
                "invitation_message": "Wear something fun",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "scheduled" and body["announced_at"] is not None
        assert body["is_surprise"] is False and body["reveal_at"] is None
        assert body["invitation_message"] == "Wear something fun"
        assert body["weather"] is not None  # revalidated
        assert len(await db_legs(w, itinerary_id)) == 2
        assert hooked == [itinerary_id]

        seen = await w.partner.get(f"{ITINERARIES}/{itinerary_id}")
        assert seen.status_code == 200 and seen.json()["status"] == "scheduled"

        # Re-scheduling a scheduled date is allowed and keeps the first announcement time.
        first_announced = body["announced_at"]
        again = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": when(15).isoformat(), "mode": "announce"},
        )
        assert again.status_code == 200
        assert again.json()["announced_at"] == first_announced
        assert hooked == [itinerary_id, itinerary_id]


async def test_schedule_surprise_hides_the_date_and_sets_reveal_time(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        start = when(9)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"starts_at": start.isoformat(), "mode": "surprise"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "scheduled" and body["is_surprise"] is True
        assert dt.datetime.fromisoformat(body["reveal_at"]) == start
        assert body["announced_at"] is None
        assert (await w.partner.get(f"{ITINERARIES}/{itinerary_id}")).status_code == 404
        assert (await w.partner.get(ITINERARIES)).json() == []
        assert (await w.owner.get(ITINERARIES)).json()[0]["is_surprise"] is True


async def test_announced_to_surprise_only_while_unseen(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        url = f"{ITINERARIES}/{itinerary_id}/schedule"
        payload = {"starts_at": when(9).isoformat()}
        assert (
            await call(w.owner, "POST", url, {**payload, "mode": "announce"})
        ).status_code == 200

        # Unseen: the planner may still turn it into a surprise (and the partner loses it).
        switched = await call(w.owner, "POST", url, {**payload, "mode": "surprise"})
        assert switched.status_code == 200 and switched.json()["is_surprise"] is True
        assert (await w.partner.get(f"{ITINERARIES}/{itinerary_id}")).status_code == 404
        # ... and back, after which the partner sees it.
        assert (
            await call(w.owner, "POST", url, {**payload, "mode": "announce"})
        ).status_code == 200
        assert (await w.partner.get(f"{ITINERARIES}/{itinerary_id}")).status_code == 200

        blocked = await call(w.owner, "POST", url, {**payload, "mode": "surprise"})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "already_seen"
        assert (await db_itinerary(w, itinerary_id)).is_surprise is False


async def test_schedule_and_cancel_state_rules(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(w)
        url = f"{ITINERARIES}/{itinerary_id}"
        bad = await call(
            w.owner,
            "POST",
            f"{url}/schedule",
            {"starts_at": when(3).isoformat(), "ends_at": when(2).isoformat(), "mode": "announce"},
        )
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_times"
        assert (
            await call(w.owner, "POST", f"{url}/schedule", {"mode": "announce"})
        ).status_code == 422
        assert (
            await call(
                w.owner, "POST", f"{url}/schedule", {"starts_at": when().isoformat(), "mode": "x"}
            )
        ).status_code == 422

        cancelled = await call(w.owner, "POST", f"{url}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        again = await call(
            w.owner,
            "POST",
            f"{url}/schedule",
            {"starts_at": when().isoformat(), "mode": "announce"},
        )
        assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_status"

        done = await seed(w, status="completed")
        assert (await call(w.owner, "POST", f"{ITINERARIES}/{done}/cancel")).status_code == 409


async def test_react_is_for_the_partner_on_a_visible_date(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        visible = await seed(w, status="scheduled", announced_at=when(-1), starts_at=when())
        hidden = await seed(w)
        url = f"{ITINERARIES}/{visible}/react"
        suggestion = when(11, 19)

        heart = await call(w.partner, "POST", url, {"kind": "heart"})
        assert heart.status_code == 200, heart.text
        assert heart.json()["partner_reaction"]["kind"] == "heart"
        assert heart.json()["is_planner"] is False

        moved = await call(
            w.partner,
            "POST",
            url,
            {
                "kind": "move",
                "suggested_start": suggestion.isoformat(),
                "note": "Friday works better",
            },
        )
        reaction = moved.json()["partner_reaction"]
        assert reaction["kind"] == "move" and reaction["note"] == "Friday works better"
        assert dt.datetime.fromisoformat(reaction["suggested_start"]) == suggestion
        assert reaction["at"]

        # The planner sees the reaction but can't react to their own date.
        planner_view = (await w.owner.get(f"{ITINERARIES}/{visible}")).json()
        assert planner_view["partner_reaction"]["kind"] == "move"
        forbidden = await call(w.owner, "POST", url, {"kind": "heart"})
        assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "forbidden"

        assert (
            await call(w.partner, "POST", f"{ITINERARIES}/{hidden}/react", {"kind": "heart"})
        ).status_code == 404
        for body in ({"kind": "wow"}, {"kind": "move", "note": "x" * 301}, {}):
            assert (await call(w.partner, "POST", url, body)).status_code == 422


# --- Venue search


async def test_places_search_near_home(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)
        places = CountingPlaces()
        db_app.dependency_overrides[get_places] = lambda: places
        try:
            response = await w.owner.get("/api/v1/places/search", params={"q": "dinner"})
            assert response.status_code == 200, response.text
            hits = response.json()
            assert 0 < len(hits) <= 8
            hit = hits[0]
            assert set(hit) == {
                "place_id",
                "name",
                "address",
                "rating",
                "price_level",
                "lat",
                "lng",
                "open_now",
                "attributions",
            }
            assert hit["place_id"].startswith("demo-place-") and hit["attributions"]
            assert hit["open_now"] in (True, False, None)
            assert places.search_calls[0]["radius_m"] == 15_000
            assert places.search_calls[0]["max_results"] == 8
            assert places.search_calls[0]["open_at"] is None

            await call(w.owner, "PUT", "/api/v1/couple/constraints", {"max_radius_km": 5})
            open_at = when(3, 20)
            await w.partner.get(
                "/api/v1/places/search", params={"q": "cafe", "open_at": open_at.isoformat()}
            )
            assert places.search_calls[1]["radius_m"] == 5_000
            assert places.search_calls[1]["open_at"] == open_at

            for params in ({"q": ""}, {"q": "x" * 121}, {}):
                assert (
                    await w.owner.get("/api/v1/places/search", params=params)
                ).status_code == 422
        finally:
            db_app.dependency_overrides.pop(get_places, None)


async def test_places_search_without_a_home_area_is_409(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        db_app.dependency_overrides[get_places] = lambda: FakePlaces()
        try:
            response = await w.owner.get("/api/v1/places/search", params={"q": "dinner"})
        finally:
            db_app.dependency_overrides.pop(get_places, None)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "home_area_required"


async def test_places_search_provider_failures_are_503(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        await set_home(w)

        def unavailable() -> Any:
            raise ApiError(503, "provider_unavailable", "Place search isn't available right now.")

        for override in (lambda: DownPlaces(), unavailable):
            db_app.dependency_overrides[get_places] = override
            try:
                response = await w.owner.get("/api/v1/places/search", params={"q": "dinner"})
            finally:
                db_app.dependency_overrides.pop(get_places, None)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "provider_unavailable"

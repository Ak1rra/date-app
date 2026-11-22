"""Rescheduling and editing re-run validation (FR-CAL-3, FR-ITIN-4, FR-ITIN-5): warnings are
rebuilt from the current stops and times, not carried over."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.models import Itinerary, Stop, SurpriseHint
from app.providers.types import Place
from app.services import itineraries as service
from app.services.surprise import process_due
from tests.authutil import call
from tests.conftest import make_settings
from tests.itinerary_helpers import (
    GELATO,
    ITINERARIES,
    TAQUERIA,
    CountingPlaces,
    DownPlaces,
    World,
    fake_providers,
    seed,
    when,
    world,
)
from tests.test_itineraries_api import snapshot

pytestmark = pytest.mark.db

# Taqueria Sol is open 10:00-21:00 every day, Gelato Lane 12:00-22:00 (providers/places/fake.py).
# The couple's timezone is UTC, so the hours below are wall times.


async def two_stops(w: World, *, day: int = 10, taqueria_hour: int = 12) -> uuid.UUID:
    """A scheduled date: a taqueria stop, then a gelato stop an hour later."""
    stops = [
        {
            "title": "Tacos",
            "place_id": TAQUERIA,
            "venue": await snapshot(TAQUERIA),
            "starts_at": when(day, taqueria_hour),
            "ends_at": when(day, taqueria_hour + 1),
        },
        {
            "title": "Gelato",
            "place_id": GELATO,
            "venue": await snapshot(GELATO),
            "starts_at": when(day, taqueria_hour + 2),
            "ends_at": when(day, taqueria_hour + 3),
        },
    ]
    return await seed(
        w,
        stops=stops,
        status="scheduled",
        announced_at=when(-1),
        starts_at=when(day, taqueria_hour),
        ends_at=when(day, taqueria_hour + 3),
    )


async def stored_warnings(w: World, itinerary_id: uuid.UUID) -> list[dict[str, Any]]:
    async with w.app.state.sessionmaker() as session:
        itinerary = await session.get(Itinerary, itinerary_id)
        assert itinerary is not None
        return list(itinerary.warnings)


async def put_stops(
    w: World, itinerary_id: uuid.UUID, order: list[int], hours: dict[int, int] | None = None
) -> list[dict[str, Any]]:
    """PUT the stops in `order` (indexes into the current order); `hours[i]` moves stop i to
    start at that hour. Returns the response's warnings."""
    url = f"{ITINERARIES}/{itinerary_id}"
    current = (await w.owner.get(url)).json()["stops"]
    items = []
    for i in order:
        stop = current[i]
        start = dt.datetime.fromisoformat(stop["starts_at"])
        end = dt.datetime.fromisoformat(stop["ends_at"])
        if hours and i in hours:
            length = end - start
            start = start.replace(hour=hours[i], minute=0)
            end = start + length
        items.append(
            {
                "id": stop["id"],
                "title": stop["title"],
                "place_id": stop["place_id"],
                "starts_at": start.isoformat(),
                "ends_at": end.isoformat(),
            }
        )
    response = await call(w.owner, "PUT", f"{url}/stops", {"stops": items})
    assert response.status_code == 200, response.text
    return response.json()["warnings"]


async def test_new_stop_times_in_closed_hours_add_a_warning_and_moving_back_clears_it(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        assert await put_stops(w, itinerary_id, [0, 1]) == []

        warnings = await put_stops(w, itinerary_id, [0, 1], hours={0: 22})
        assert [(x["stop_index"], x["code"]) for x in warnings if x["code"] == "closed"] == [
            (0, "closed")
        ]
        assert set(warnings[0]) == {"stop_index", "code", "message"}
        assert await stored_warnings(w, itinerary_id) == warnings

        assert await put_stops(w, itinerary_id, [0, 1], hours={0: 12}) == []
        assert await stored_warnings(w, itinerary_id) == []


async def stop_times(w: World, itinerary_id: uuid.UUID) -> list[tuple[dt.datetime, dt.datetime]]:
    async with w.app.state.sessionmaker() as session:
        rows = await session.execute(
            select(Stop).where(Stop.itinerary_id == itinerary_id).order_by(Stop.position)
        )
        return [(s.starts_at, s.ends_at) for s in rows.scalars()]  # type: ignore[misc]


def schedule_body(start: dt.datetime, end: dt.datetime | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"mode": "announce", "starts_at": start.isoformat()}
    if end is not None:
        body["ends_at"] = end.isoformat()
    return body


async def test_rescheduling_a_day_later_moves_every_stop_and_keeps_the_length(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        before = await stop_times(w, itinerary_id)
        day = dt.timedelta(days=1)

        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            schedule_body(when(11, 12)),
        )
        assert response.status_code == 200, response.text
        out = response.json()
        assert dt.datetime.fromisoformat(out["starts_at"]) == when(11, 12)
        assert dt.datetime.fromisoformat(out["ends_at"]) == when(11, 15)  # still 3 hours
        assert await stop_times(w, itinerary_id) == [(a + day, b + day) for a, b in before]
        assert out["warnings"] == []


async def test_rescheduling_into_closed_hours_adds_the_warning_and_moving_back_clears_it(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        url = f"{ITINERARIES}/{itinerary_id}/schedule"

        late = await call(w.owner, "POST", url, schedule_body(when(10, 22)))  # Tacos at 22:00
        assert late.status_code == 200, late.text
        assert (0, "closed") in {(x["stop_index"], x["code"]) for x in late.json()["warnings"]}
        assert await stored_warnings(w, itinerary_id) == late.json()["warnings"]

        back = await call(w.owner, "POST", url, schedule_body(when(10, 12)))
        assert back.status_code == 200, back.text
        assert back.json()["warnings"] == []
        assert await stored_warnings(w, itinerary_id) == []


async def test_an_explicit_end_is_kept_when_rescheduling(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            schedule_body(when(11, 12), when(11, 20)),
        )
        assert response.status_code == 200, response.text
        assert dt.datetime.fromisoformat(response.json()["ends_at"]) == when(11, 20)


async def test_patching_the_start_moves_the_stops_and_the_end_too(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        before = await stop_times(w, itinerary_id)
        hour = dt.timedelta(hours=1)

        response = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}",
            {"starts_at": when(10, 13).isoformat()},
        )
        assert response.status_code == 200, response.text
        out = response.json()
        assert dt.datetime.fromisoformat(out["ends_at"]) == when(10, 16)
        assert await stop_times(w, itinerary_id) == [(a + hour, b + hour) for a, b in before]


async def test_a_draft_with_no_start_yet_does_not_move_its_stops(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            stops=[
                {"title": "Walk", "starts_at": when(10, 12), "ends_at": when(10, 13)},
                {"title": "Dinner", "starts_at": when(10, 14), "ends_at": when(10, 16)},
            ],
        )
        before = await stop_times(w, itinerary_id)
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            schedule_body(when(12, 18)),
        )
        assert response.status_code == 200, response.text
        assert await stop_times(w, itinerary_id) == before
        assert response.json()["ends_at"] is None


async def test_a_patched_time_on_a_scheduled_date_replaces_stale_warnings(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        stale = [{"stop_index": 0, "code": "closed", "message": "Seeded by hand."}]
        async with w.app.state.sessionmaker() as session:
            itinerary = await session.get(Itinerary, itinerary_id)
            assert itinerary is not None
            itinerary.warnings = stale
            await session.commit()

        response = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}",
            {"starts_at": when(10, 11).isoformat(), "ends_at": when(10, 16).isoformat()},
        )
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == []


async def test_reordering_stops_updates_stop_index(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        # Tacos at 22:00 is closed; Gelato at 14:00 (open 12:00-22:00) is not.
        warnings = await put_stops(w, itinerary_id, [0, 1], hours={0: 22, 1: 14})
        assert [x["stop_index"] for x in warnings if x["code"] == "closed"] == [0]

        reordered = await put_stops(w, itinerary_id, [1, 0])
        assert [x["stop_index"] for x in reordered if x["code"] == "closed"] == [1]


async def test_a_provider_outage_keeps_the_request_ok_and_adds_no_hours_warning(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        w.providers["p"] = fake_providers(places=DownPlaces())

        # The kept place ids keep their stored venue, so the PUT needs no lookup; the closed
        # time can't be checked while the provider is down.
        warnings = await put_stops(w, itinerary_id, [0, 1], hours={0: 22})
        assert not [x for x in warnings if x["code"] in ("closed", "unknown_place", "ungrounded")]

        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {
                "mode": "announce",
                "starts_at": when(10, 12).isoformat(),
                "ends_at": when(10, 15).isoformat(),
            },
        )
        assert response.status_code == 200, response.text


async def test_stops_with_no_times_are_skipped_and_a_hand_added_stop_is_not_flagged(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            status="scheduled",
            announced_at=when(-1),
            starts_at=when(10, 12),
            stops=[
                {"title": "Walk", "starts_at": when(10, 12), "ends_at": when(10, 13)},
                {"title": "Undecided"},
                {
                    "title": "Stroll",
                    "evergreen": True,
                    "starts_at": when(10, 14),
                    "ends_at": when(10, 15),
                },
            ],
        )
        response = await call(
            w.owner,
            "POST",
            f"{ITINERARIES}/{itinerary_id}/schedule",
            {"mode": "announce", "starts_at": when(10, 12).isoformat()},
        )
        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == []


async def test_a_stop_added_by_hand_with_no_place_gets_no_warning(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await two_stops(w)
        url = f"{ITINERARIES}/{itinerary_id}"
        current = (await w.owner.get(url)).json()["stops"]
        items = [
            {
                "id": s["id"],
                "title": s["title"],
                "place_id": s["place_id"],
                "starts_at": s["starts_at"],
                "ends_at": s["ends_at"],
            }
            for s in current
        ]
        items.insert(
            1,
            {
                "title": "Walk",
                "starts_at": when(10, 13).isoformat(),
                "ends_at": when(10, 14).isoformat(),
            },
        )
        response = await call(w.owner, "PUT", f"{url}/stops", {"stops": items})
        assert response.status_code == 200, response.text
        assert len(response.json()["stops"]) == 3
        assert response.json()["warnings"] == []


class CountingDownPlaces(DownPlaces):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def details(self, place_id: str) -> Place:
        self.calls.append(place_id)
        return await super().details(place_id)


async def test_viewing_during_an_outage_looks_each_place_up_once(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        places = CountingDownPlaces()
        w.providers["p"] = fake_providers(places=places)
        stops = [
            {
                "title": f"Stop {n}",
                "place_id": place_id,
                "starts_at": when(10, 12 + n),
                "ends_at": when(10, 12 + n) + dt.timedelta(minutes=30),
            }
            for n, place_id in enumerate((TAQUERIA, GELATO, TAQUERIA, GELATO))
        ]
        itinerary_id = await seed(w, status="scheduled", starts_at=when(10, 12), stops=stops)
        response = await w.owner.get(f"{ITINERARIES}/{itinerary_id}")
        assert response.status_code == 200, response.text
        assert len(response.json()["stops"]) == 4
        assert sorted(places.calls) == sorted({TAQUERIA, GELATO})


async def test_a_redacted_stop_triggers_no_place_lookup(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        places = CountingPlaces()
        w.providers["p"] = fake_providers(places=places)
        itinerary_id = await seed(
            w,
            is_surprise=True,
            status="scheduled",
            starts_at=when(5, 12),
            reveal_at=when(-1),
            revealed_at=when(-1),
            stops=[
                {
                    "title": "Visible",
                    "place_id": TAQUERIA,
                    "starts_at": when(5, 12),
                    "ends_at": when(5, 13),
                },
                {
                    "title": "Hidden",
                    "place_id": GELATO,
                    "reveal_policy": "at_stop_time",
                    "starts_at": when(5, 14),
                    "ends_at": when(5, 15),
                },
            ],
        )
        response = await w.partner.get(f"{ITINERARIES}/{itinerary_id}")
        assert response.status_code == 200, response.text
        assert response.json()["stops"][1]["title"] != "Hidden"
        assert places.details_calls == [TAQUERIA]


async def warnings_now(w: World, itinerary_id: uuid.UUID, places: Any) -> list[dict[str, Any]]:
    async with w.app.state.sessionmaker() as session:
        itinerary = await session.get(Itinerary, itinerary_id)
        assert itinerary is not None
        return await service.compute_warnings(
            session, itinerary, fake_providers(places=places), make_settings()
        )


async def test_an_outage_looks_each_place_up_once_and_adds_no_place_warnings(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        stops = [
            {
                "title": f"Stop {n}",
                "place_id": place_id,
                "starts_at": when(10, 22) + dt.timedelta(minutes=30 * n),
                "ends_at": when(10, 22) + dt.timedelta(minutes=30 * n + 20),
            }
            for n, place_id in enumerate((TAQUERIA, GELATO, TAQUERIA, GELATO))
        ]
        itinerary_id = await seed(
            w, status="scheduled", starts_at=when(10, 22), ends_at=when(11, 1), stops=stops
        )
        places = CountingDownPlaces()
        warnings = await warnings_now(w, itinerary_id, places)
        assert sorted(places.calls) == sorted({TAQUERIA, GELATO})
        assert not [x for x in warnings if x["code"] in ("closed", "unknown_place")]


async def test_an_outage_and_repair_only_issues_leave_no_warnings(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            status="scheduled",
            starts_at=when(10, 12),
            ends_at=when(10, 23),
            stops=[
                {"title": "Walk", "starts_at": when(10, 12), "ends_at": when(10, 13)},
                {
                    "title": "Mixed up",
                    "place_id": GELATO,
                    "evergreen": True,
                    "starts_at": when(10, 14),
                    "ends_at": when(10, 15),
                },
                {
                    "title": "Tacos",
                    "place_id": TAQUERIA,
                    "evergreen": False,
                    "starts_at": when(10, 22),
                    "ends_at": when(10, 23),
                },
            ],
        )
        warnings = await warnings_now(w, itinerary_id, DownPlaces())
        # No place-dependent warning for the failed lookup, and nothing worded for the AI.
        assert warnings == []


async def test_changing_only_the_budget_of_a_scheduled_date_revalidates(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        stops = [
            {
                "title": "Tacos",
                "place_id": TAQUERIA,
                "est_cost": 40,
                "starts_at": when(10, 12),
                "ends_at": when(10, 13),
            },
            {
                "title": "Gelato",
                "place_id": GELATO,
                "est_cost": 30,
                "starts_at": when(10, 14),
                "ends_at": when(10, 15),
            },
        ]
        itinerary_id = await seed(
            w, status="scheduled", starts_at=when(10, 12), budget=100, stops=stops
        )
        url = f"{ITINERARIES}/{itinerary_id}"
        low = await call(w.owner, "PATCH", url, {"budget": 50})
        assert low.status_code == 200, low.text
        assert [(x["stop_index"], x["code"]) for x in low.json()["warnings"]] == [
            (None, "over_budget")
        ]
        high = await call(w.owner, "PATCH", url, {"budget": 100})
        assert high.json()["warnings"] == []


async def test_patching_a_surprise_earlier_releases_what_is_now_due(db_app: FastAPI) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            is_surprise=True,
            status="scheduled",
            starts_at=when(5, 18),
            ends_at=when(5, 21),
            reveal_at=when(5, 18),
            stops=[{"title": "Dinner", "starts_at": when(5, 18), "ends_at": when(5, 20)}],
        )
        async with w.app.state.sessionmaker() as session:
            session.add(
                SurpriseHint(
                    itinerary_id=itinerary_id, kind="custom", text="Soon", release_at=when(4, 18)
                )
            )
            await session.commit()

        # Six days earlier: the hint's shifted time and the reveal time are both in the past.
        response = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}",
            {"starts_at": when(-1, 18).isoformat()},
        )
        assert response.status_code == 200, response.text
        async with w.app.state.sessionmaker() as session:
            itinerary = await session.get(Itinerary, itinerary_id)
            assert itinerary is not None and itinerary.revealed_at is not None
            hint = (await session.execute(select(SurpriseHint))).scalar_one()
            assert hint.released_at is not None


async def test_patching_a_surprise_moves_its_reveal_and_unreleased_hints(
    db_app: FastAPI,
) -> None:
    async with world(db_app) as w:
        itinerary_id = await seed(
            w,
            title="Secret rooftop dinner",
            is_surprise=True,
            status="scheduled",
            starts_at=when(5, 18),
            ends_at=when(5, 21),
            reveal_at=when(5, 18),
            stops=[{"title": "Dinner", "starts_at": when(5, 18), "ends_at": when(5, 20)}],
        )
        async with w.app.state.sessionmaker() as session:
            session.add_all(
                [
                    SurpriseHint(
                        itinerary_id=itinerary_id,
                        kind="custom",
                        text="Pending",
                        release_at=when(4, 18),
                    ),
                    SurpriseHint(
                        itinerary_id=itinerary_id,
                        kind="custom",
                        text="Sent",
                        release_at=when(3, 18),
                        released_at=when(3, 18),
                    ),
                ]
            )
            await session.commit()

        response = await call(
            w.owner,
            "PATCH",
            f"{ITINERARIES}/{itinerary_id}",
            {"starts_at": when(6, 18).isoformat()},
        )
        assert response.status_code == 200, response.text

        async with w.app.state.sessionmaker() as session:
            itinerary = await session.get(Itinerary, itinerary_id)
            assert itinerary is not None
            assert itinerary.reveal_at == when(6, 18)
            rows = await session.execute(
                select(SurpriseHint).where(SurpriseHint.itinerary_id == itinerary_id)
            )
            assert {h.text: h.release_at for h in rows.scalars()} == {
                "Pending": when(5, 18),
                "Sent": when(3, 18),
            }

            # A job queued for the old reveal time finds nothing to do.
            await process_due(session, itinerary, make_settings(), when(5, 19))
            await session.commit()
            assert itinerary.revealed_at is None
        assert (await w.partner.get(f"{ITINERARIES}/{itinerary_id}")).status_code == 404

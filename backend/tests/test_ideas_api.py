"""Ideas: my private ideas, sharing, the output shape and the ideas.expire job."""

import datetime as dt
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Couple, CoupleMember, Idea, Job, User
from app.worker import handlers
from tests.authutil import call, joined_couple, sign_in

pytestmark = pytest.mark.db

IDEAS = "/api/v1/ideas"
OWNER = "ann@example.com"
PARTNER = "bea@example.com"


async def add_idea(client: AsyncClient, title: str = "Picnic", **fields: Any) -> dict[str, Any]:
    response = await call(client, "POST", IDEAS, {"title": title, **fields})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def share(client: AsyncClient, idea_id: str) -> dict[str, Any]:
    response = await call(client, "POST", f"{IDEAS}/{idea_id}/share")
    assert response.status_code == 200, response.text
    return dict(response.json())


async def shared_idea(client: AsyncClient, title: str = "Picnic", **fields: Any) -> dict[str, Any]:
    return await share(client, (await add_idea(client, title, **fields))["id"])


async def titles(client: AsyncClient, **params: str) -> list[str]:
    response = await client.get(IDEAS, params=params)
    assert response.status_code == 200, response.text
    return [i["title"] for i in response.json()]


async def user_id(db: AsyncSession, email: str) -> uuid.UUID:
    return (await db.execute(select(User.id).where(User.email == email))).scalar_one()


async def couple_id(db: AsyncSession, email: str = OWNER) -> uuid.UUID:
    return (
        await db.execute(
            select(CoupleMember.couple_id)
            .join(User, User.id == CoupleMember.user_id)
            .where(User.email == email)
        )
    ).scalar_one()


async def insert_idea(db: AsyncSession, **fields: Any) -> Idea:
    """A shared active idea straight into the database, by the owner, unless told otherwise."""
    values: dict[str, Any] = {
        "couple_id": await couple_id(db),
        "created_by_user_id": await user_id(db, OWNER),
        "source": "ai",
        "visibility": "shared",
        "title": "Inserted",
        "pitch": "",
    }
    values.update(fields)
    idea = Idea(**values)
    db.add(idea)
    await db.commit()
    return idea


def error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


async def test_every_route_needs_a_signed_in_user(db_client: AsyncClient) -> None:
    assert (await db_client.get(IDEAS)).status_code == 401
    assert (await call(db_client, "POST", IDEAS, {"title": "x"})).status_code == 401
    assert (await db_client.get("/api/v1/deck")).status_code == 401
    assert (await db_client.get(f"{IDEAS}/lists")).status_code == 401


async def test_create_makes_a_private_manual_idea_and_lists_it(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, OWNER)
    assert await titles(db_client) == []
    idea = await add_idea(
        db_client,
        "  Pottery class ",
        pitch="Make mugs",
        tags=["creative"],
        category="activity",
        est_cost=45.5,
        duration_minutes=90,
        best_time="evening",
        indoor_outdoor="indoor",
    )
    assert idea["title"] == "Pottery class"
    assert idea["source"] == "manual" and idea["visibility"] == "private"
    assert idea["status"] == "active" and idea["grounded"] is False
    assert idea["created_by_me"] is True
    assert idea["est_cost"] == 45.5 and idea["currency"] == "USD"
    assert idea["tags"] == ["creative"] and idea["duration_minutes"] == 90
    assert idea["venue"] is None and idea["event"] is None
    assert idea["may_have_changed"] is False and idea["attributions"] == []
    assert idea["votes"] == {"mine": None, "partner": None}
    assert await titles(db_client) == ["Pottery class"]
    assert await titles(db_client, visibility="shared") == []
    assert await titles(db_client, status="archived") == []


@pytest.mark.parametrize(
    "body",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": "ok", "pitch": "x" * 2001},
        {"title": "ok", "tags": [str(n) for n in range(11)]},
        {"title": "ok", "est_cost": -1},
        {"title": "ok", "duration_minutes": 14},
        {"title": "ok", "duration_minutes": 1441},
        {"title": "ok", "best_time": "midnight"},
        {"title": "ok", "indoor_outdoor": "underwater"},
        {"pitch": "no title"},
    ],
)
async def test_create_validates(
    db_app: FastAPI, db_client: AsyncClient, body: dict[str, Any]
) -> None:
    await sign_in(db_app, db_client, OWNER)
    assert (await call(db_client, "POST", IDEAS, body)).status_code == 422


async def test_a_manual_idea_keeps_an_optional_venue_name_and_link(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, OWNER)
    idea = await add_idea(
        db_client, "Rooftop drinks", venue_name="  Sky Bar ", link="https://example.com/sky"
    )
    assert idea["grounded"] is False and idea["attributions"] == []
    assert idea["venue"]["name"] == "Sky Bar" and idea["venue"]["url"] == "https://example.com/sky"
    listed = (await call(db_client, "GET", IDEAS)).json()
    assert [i["venue"] for i in listed] == [idea["venue"]]

    only_link = await add_idea(db_client, "Link only", link="http://example.com/x")
    assert only_link["venue"]["name"] is None
    assert only_link["venue"]["url"] == "http://example.com/x"
    assert (await add_idea(db_client, "Plain"))["venue"] is None


@pytest.mark.parametrize(
    "fields",
    [
        {"link": "not a url"},
        {"link": "ftp://example.com/file"},
        {"link": "javascript:alert(1)"},
        {"link": "https://example.com/" + "a" * 500},
        {"venue_name": "x" * 121},
    ],
)
async def test_create_rejects_a_bad_link_or_venue_name(
    db_app: FastAPI, db_client: AsyncClient, fields: dict[str, Any]
) -> None:
    await sign_in(db_app, db_client, OWNER)
    response = await call(db_client, "POST", IDEAS, {"title": "ok", **fields})
    assert response.status_code == 422, response.text


async def test_patch_sets_and_clears_the_venue_name_and_link(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, OWNER)
    idea = await add_idea(db_client, "Bar", venue_name="Sky Bar", link="https://example.com/sky")
    url = f"{IDEAS}/{idea['id']}"
    body = (await call(db_client, "PATCH", url, {"venue_name": "Moon Bar"})).json()
    assert body["venue"]["name"] == "Moon Bar" and body["venue"]["url"] == "https://example.com/sky"
    body = (await call(db_client, "PATCH", url, {"link": None})).json()
    assert body["venue"]["name"] == "Moon Bar" and body["venue"]["url"] is None
    assert (await call(db_client, "PATCH", url, {"link": "ftp://x.com"})).status_code == 422
    body = (await call(db_client, "PATCH", url, {"venue_name": None})).json()
    assert body["venue"] is None
    body = (await call(db_client, "PATCH", url, {"title": "Bar night"})).json()
    assert body["venue"] is None


async def test_list_filters_are_validated(db_app: FastAPI, db_client: AsyncClient) -> None:
    await sign_in(db_app, db_client, OWNER)
    assert (await db_client.get(IDEAS, params={"visibility": "public"})).status_code == 422
    assert (await db_client.get(IDEAS, params={"status": "gone"})).status_code == 422


async def test_a_private_idea_is_a_404_for_the_other_member_on_every_route(
    db_app: FastAPI,
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        idea_id = (await add_idea(owner, "Secret"))["id"]
        url = f"{IDEAS}/{idea_id}"
        assert await titles(partner) == []
        assert await titles(partner, visibility="shared") == []
        assert (await call(partner, "PATCH", url, {"title": "Mine now"})).status_code == 404
        assert (await call(partner, "DELETE", url)).status_code == 404
        assert (await call(partner, "POST", f"{url}/share")).status_code == 404
        assert (await call(partner, "PUT", f"{url}/vote", {"value": "yes"})).status_code == 404
        assert (await partner.get("/api/v1/deck")).json()["cards"] == []
        # Untouched, and still the owner's.
        assert await titles(owner) == ["Secret"]


async def test_an_unknown_idea_is_a_404(db_app: FastAPI, db_client: AsyncClient) -> None:
    await sign_in(db_app, db_client, OWNER)
    url = f"{IDEAS}/{uuid.uuid4()}"
    assert (await call(db_client, "PATCH", url, {"title": "x"})).status_code == 404
    assert (await call(db_client, "DELETE", url)).status_code == 404
    assert (await call(db_client, "POST", f"{url}/share")).status_code == 404
    assert (await call(db_client, "PUT", f"{url}/vote", {"value": "yes"})).status_code == 404


async def test_patch_changes_only_what_is_sent(db_app: FastAPI, db_client: AsyncClient) -> None:
    await sign_in(db_app, db_client, OWNER)
    idea = await add_idea(
        db_client, "Walk", pitch="By the river", category="outdoors", est_cost=0, tags=["free"]
    )
    url = f"{IDEAS}/{idea['id']}"
    response = await call(db_client, "PATCH", url, {"title": "Long walk", "est_cost": 12})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Long walk" and body["est_cost"] == 12
    assert body["pitch"] == "By the river" and body["tags"] == ["free"]
    assert body["category"] == "outdoors"

    cleared = (await call(db_client, "PATCH", url, {"category": None, "est_cost": None})).json()
    assert cleared["category"] is None and cleared["est_cost"] is None
    assert cleared["title"] == "Long walk"

    for bad in ({"title": None}, {"pitch": None}, {"tags": None}, {"title": ""}):
        assert (await call(db_client, "PATCH", url, bad)).status_code == 422


async def test_patch_sets_updated_at(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, OWNER)
    idea = await add_idea(db_client)
    await db_session.execute(update(Idea).values(updated_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC)))
    await db_session.commit()
    await call(db_client, "PATCH", f"{IDEAS}/{idea['id']}", {"pitch": "Updated"})
    row = (await db_session.execute(select(Idea))).scalar_one()
    await db_session.refresh(row)
    assert row.updated_at.year >= 2026 and row.pitch == "Updated"


async def test_delete_archives_instead_of_deleting(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, OWNER)
    idea = await add_idea(db_client, "Old idea")
    response = await call(db_client, "DELETE", f"{IDEAS}/{idea['id']}")
    assert response.status_code == 204
    assert await titles(db_client) == []
    assert await titles(db_client, status="archived") == ["Old idea"]
    assert (await db_session.execute(select(Idea.status))).scalar_one() == "archived"
    # Archiving again is harmless.
    assert (await call(db_client, "DELETE", f"{IDEAS}/{idea['id']}")).status_code == 204


async def test_share_is_creator_only_and_irreversible(
    db_app: FastAPI, db_session: AsyncSession
) -> None:
    async with joined_couple(db_app) as (owner, partner):
        idea = await add_idea(owner, "Rooftop dinner")
        assert (await call(partner, "POST", f"{IDEAS}/{idea['id']}/share")).status_code == 404
        assert await titles(partner, visibility="shared") == []

        shared = await share(owner, idea["id"])
        assert shared["visibility"] == "shared" and shared["id"] == idea["id"]
        row = (await db_session.execute(select(Idea))).scalar_one()
        stamp = row.shared_at
        assert stamp is not None

        # Sharing again, by anyone who can see it, changes nothing.
        assert (await share(owner, idea["id"]))["visibility"] == "shared"
        assert (await share(partner, idea["id"]))["visibility"] == "shared"
        await db_session.refresh(row)
        assert row.shared_at == stamp

        # The owner's private list no longer has it; the couple's shared list does.
        assert await titles(owner) == []
        assert await titles(owner, visibility="shared") == ["Rooftop dinner"]
        seen = (await partner.get(IDEAS, params={"visibility": "shared"})).json()
        assert [(i["title"], i["created_by_me"]) for i in seen] == [("Rooftop dinner", False)]
        assert await titles(partner) == []


async def test_a_shared_idea_is_editable_and_archivable_by_either_member(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        idea = await shared_idea(owner, "Bowling")
        url = f"{IDEAS}/{idea['id']}"
        response = await call(partner, "PATCH", url, {"title": "Mini golf"})
        assert response.status_code == 200 and response.json()["title"] == "Mini golf"
        assert response.json()["created_by_me"] is False
        assert (await call(partner, "DELETE", url)).status_code == 204
        assert await titles(owner, visibility="shared") == []
        assert await titles(owner, visibility="shared", status="archived") == ["Mini golf"]


async def test_another_couples_ideas_are_a_404(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, _):
        idea = await shared_idea(owner, "Ours")
        async with joined_couple(db_app, "cy@example.com", "dee@example.com") as (cy, _dee):
            url = f"{IDEAS}/{idea['id']}"
            assert (await call(cy, "PATCH", url, {"title": "x"})).status_code == 404
            assert (await call(cy, "PUT", f"{url}/vote", {"value": "yes"})).status_code == 404
            assert await titles(cy, visibility="shared") == []


async def test_output_has_venue_distance_event_staleness_and_attribution_names(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, OWNER)
    await db_session.execute(
        update(Couple).values(home_lat=40.71, home_lng=-74.01)  # lower Manhattan
    )
    await db_session.commit()
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=45)).isoformat()
    await insert_idea(
        db_session,
        title="Central Park boat",
        visibility="private",
        grounded=True,
        venue={
            "name": "The Loeb Boathouse",
            "rating": 4.4,
            "price_level": 2,
            "address": "Central Park, NYC",
            "lat": 40.78,
            "lng": -73.97,
            "fetched_at": old,
            "attributions": ["Places data (c) Google"],
            "reviews": ["never sent"],
        },
        generation={
            "event": {
                "name": "Jazz night",
                "local_date": "2030-06-01",
                "local_time": "20:00:00",
                "url": "https://example.com/jazz",
                "secret": "never sent",
            }
        },
    )
    [row] = (await db_client.get(IDEAS)).json()
    venue = row["venue"]
    assert venue["name"] == "The Loeb Boathouse" and venue["rating"] == 4.4
    assert venue["price_level"] == 2 and venue["address"] == "Central Park, NYC"
    assert 8.0 < venue["distance_km"] < 9.0  # about 8.5 km north-east
    assert set(venue) == {"name", "rating", "price_level", "address", "distance_km", "url"}
    assert row["event"] == {
        "name": "Jazz night",
        "local_date": "2030-06-01",
        "local_time": "20:00:00",
        "url": "https://example.com/jazz",
    }
    assert row["may_have_changed"] is True
    assert row["attributions"] == ["Places data (c) Google"]
    assert row["grounded"] is True


async def test_venue_distance_is_null_without_home_or_coordinates(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await sign_in(db_app, db_client, OWNER)
    fresh = dt.datetime.now(dt.UTC).isoformat()
    await insert_idea(
        db_session,
        title="No home",
        visibility="private",
        venue={"name": "A", "lat": 1.0, "lng": 2.0, "fetched_at": fresh},
    )
    [row] = (await db_client.get(IDEAS)).json()
    assert row["venue"]["distance_km"] is None  # the couple has no home yet
    assert row["may_have_changed"] is False  # fetched just now

    await db_session.execute(update(Couple).values(home_lat=10, home_lng=10))
    await db_session.commit()
    await db_session.execute(delete(Idea))
    await insert_idea(
        db_session,
        title="No coordinates",
        visibility="private",
        venue={"name": "B", "location": None},
    )
    [row] = (await db_client.get(IDEAS)).json()
    assert row["venue"]["distance_km"] is None
    assert row["may_have_changed"] is False  # no snapshot time


async def test_expire_job_flips_only_active_grounded_ideas_past_their_date(
    db_app: FastAPI,
    db_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await sign_in(db_app, db_client, OWNER)
    past = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    future = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
    wanted = {
        "expires": {"grounded": True, "valid_until": past},
        "future": {"grounded": True, "valid_until": future},
        "ungrounded": {"grounded": False, "valid_until": past},
        "no date": {"grounded": True, "valid_until": None},
        "archived": {"grounded": True, "valid_until": past, "status": "archived"},
    }
    for title, fields in wanted.items():
        await insert_idea(db_session, title=title, **fields)

    monkeypatch.setattr(handlers, "_utcnow", lambda: dt.datetime(2099, 3, 4, 10, tzinfo=dt.UTC))
    await handlers.ideas_expire(db_session, {})
    await db_session.commit()

    db_session.expire_all()
    rows = (await db_session.execute(select(Idea.title, Idea.status))).all()
    assert dict(rows) == {
        "expires": "expired",
        "future": "active",
        "ungrounded": "active",
        "no date": "active",
        "archived": "archived",
    }
    # It queued the next day's run, due at midnight UTC.
    job = (
        await db_session.execute(select(Job).where(Job.dedupe_key == "ideas.expire:2099-03-05"))
    ).scalar_one()
    assert job.kind == "ideas.expire" and job.run_at == dt.datetime(2099, 3, 5, tzinfo=dt.UTC)


async def test_expire_job_is_scheduled_once_per_day(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_utcnow", lambda: dt.datetime(2099, 3, 4, 10, tzinfo=dt.UTC))
    assert await handlers.schedule_ideas_expire(db_session, next_day=False) is True
    assert await handlers.schedule_ideas_expire(db_session, next_day=False) is False
    assert await handlers.schedule_ideas_expire(db_session, next_day=True) is True
    assert await handlers.schedule_ideas_expire(db_session, next_day=True) is False
    await db_session.commit()
    keys = (
        (await db_session.execute(select(Job.dedupe_key).where(Job.kind == "ideas.expire")))
        .scalars()
        .all()
    )
    assert sorted(k for k in keys if k) == ["ideas.expire:2099-03-04", "ideas.expire:2099-03-05"]

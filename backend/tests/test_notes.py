"""Planner notes: private to their author."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlannerNote
from tests.authutil import call, joined_couple, profile_ids, sign_in

pytestmark = pytest.mark.db

NOTES = "/api/v1/notes"


async def add_note(
    client: AsyncClient, about: str, text: str, kind: str = "note"
) -> dict[str, Any]:
    response = await call(
        client, "POST", NOTES, {"about_profile_id": about, "kind": kind, "text": text}
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_create_and_list_notes_newest_first(
    db_app: FastAPI, db_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = (await sign_in(db_app, db_client, "ann@example.com")).json()["user"]
    partner = (await profile_ids(db_client))["partner"]
    assert (await db_client.get(NOTES)).json() == []

    first = await add_note(db_client, partner, "  Likes tulips  ")
    second = await add_note(db_client, partner, "Mentioned a pottery class", "mentioned_idea")
    assert first["text"] == "Likes tulips"  # stripped
    assert first["kind"] == "note" and first["about_profile_id"] == partner
    assert first["archived_at"] is None
    assert first["created_at"] and first["updated_at"]

    listed = (await db_client.get(NOTES)).json()
    assert [n["id"] for n in listed] == [second["id"], first["id"]]
    only_ideas = (await db_client.get(NOTES, params={"kind": "mentioned_idea"})).json()
    assert [n["id"] for n in only_ideas] == [second["id"]]
    only_notes = (await db_client.get(NOTES, params={"kind": "note"})).json()
    assert [n["id"] for n in only_notes] == [first["id"]]
    assert (await db_client.get(NOTES, params={"kind": "gossip"})).status_code == 422

    rows = (await db_session.execute(select(PlannerNote))).scalars().all()
    assert {r.author_user_id for r in rows} == {uuid.UUID(user["id"])}


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "note", "text": ""},
        {"kind": "note", "text": "   "},
        {"kind": "note", "text": "x" * 2001},
        {"kind": "secret", "text": "hi"},
        {"kind": "note"},
    ],
)
async def test_create_note_validates(
    db_app: FastAPI, db_client: AsyncClient, body: dict[str, Any]
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    about = (await profile_ids(db_client))["partner"]
    response = await call(db_client, "POST", NOTES, {"about_profile_id": about, **body})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert (await db_client.get(NOTES)).json() == []


async def test_a_note_cannot_be_about_the_author_or_a_stranger(
    db_app: FastAPI, db_client: AsyncClient
) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    own = (await profile_ids(db_client))["owner"]
    async with joined_couple(db_app, "cat@example.com", "dan@example.com") as (_, dan):
        theirs = await profile_ids(dan)
        for about in (own, theirs["owner"], theirs["partner"], str(uuid.uuid4())):
            body = {"about_profile_id": about, "kind": "note", "text": "hi"}
            response = await call(db_client, "POST", NOTES, body)
            assert response.status_code == 422, about
            assert response.json()["error"]["code"] == "invalid_profile"
        # The joined partner is the author of her own slot, so she can only write about him.
        body = {"about_profile_id": theirs["partner"], "kind": "note", "text": "hi"}
        assert (await call(dan, "POST", NOTES, body)).json()["error"]["code"] == "invalid_profile"
        assert (await add_note(dan, theirs["owner"], "Loves hiking"))["about_profile_id"] == theirs[
            "owner"
        ]
    assert (await db_client.get(NOTES)).json() == []


async def test_patch_archive_unarchive_and_delete(db_app: FastAPI, db_client: AsyncClient) -> None:
    await sign_in(db_app, db_client, "ann@example.com")
    partner = (await profile_ids(db_client))["partner"]
    note = await add_note(db_client, partner, "Likes tulips")
    url = f"{NOTES}/{note['id']}"

    edited = await call(db_client, "PATCH", url, {"text": "Likes red tulips"})
    assert edited.status_code == 200
    assert edited.json()["text"] == "Likes red tulips"
    assert edited.json()["archived_at"] is None
    assert edited.json()["updated_at"] >= note["updated_at"]

    archived = await call(db_client, "PATCH", url, {"archived": True})
    assert archived.json()["archived_at"] is not None
    assert archived.json()["text"] == "Likes red tulips"  # untouched
    assert (await db_client.get(NOTES)).json() == []  # archived notes are not listed

    restored = await call(db_client, "PATCH", url, {"archived": False})
    assert restored.json()["archived_at"] is None
    assert [n["id"] for n in (await db_client.get(NOTES)).json()] == [note["id"]]

    for bad in ({"text": ""}, {"text": None}, {"archived": None}, {"text": "x" * 2001}):
        assert (await call(db_client, "PATCH", url, bad)).status_code == 422
    unchanged = await call(db_client, "PATCH", url, {})  # nothing to change
    assert unchanged.status_code == 200
    assert unchanged.json()["text"] == "Likes red tulips"
    assert unchanged.json()["archived_at"] is None

    assert (await call(db_client, "DELETE", url)).status_code == 204
    assert (await db_client.get(NOTES)).json() == []
    assert (await call(db_client, "DELETE", url)).status_code == 404
    assert (await call(db_client, "PATCH", url, {"text": "x"})).status_code == 404


async def test_each_member_only_sees_their_own_notes(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        ids = await profile_ids(owner)
        his = await add_note(owner, ids["partner"], "She likes tulips")
        hers = await add_note(partner, ids["owner"], "He likes tea", "mentioned_idea")

        assert [n["id"] for n in (await owner.get(NOTES)).json()] == [his["id"]]
        assert [n["id"] for n in (await partner.get(NOTES)).json()] == [hers["id"]]
        for client, other in ((owner, hers), (partner, his)):
            url = f"{NOTES}/{other['id']}"
            for method, body in (
                ("PATCH", {"text": "mine now"}),
                ("PATCH", {"archived": True}),
                ("DELETE", None),
            ):
                response = await call(client, method, url, body)
                assert response.status_code == 404
                assert response.json()["error"]["code"] == "not_found"
        assert (await partner.get(NOTES)).json()[0]["text"] == "He likes tea"
        assert (await owner.get(NOTES)).json()[0]["text"] == "She likes tulips"


async def test_openapi_does_not_advertise_null_for_note_patch_fields(
    db_client: AsyncClient,
) -> None:
    schemas = (await db_client.get("/api/v1/openapi.json")).json()["components"]["schemas"]
    assert all("anyOf" not in prop for prop in schemas["NotePatch"]["properties"].values())

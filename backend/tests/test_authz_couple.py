"""Negative authorization for the couple-space routes: FR-PAIR-4, NFR-SEC-2, FR-PROF-7/8.

Another couple's ids are always 404 (never 403), author-only notes are 404 for everyone else,
and only a profile's own subject may edit it once she has joined.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event

from tests.authutil import browser, call, joined_couple, profile_ids, sign_in

pytestmark = pytest.mark.db

API = "/api/v1"
PROFILE_BODY: dict[str, Any] = {"loves": "Hijacked", "liked_categories": ["food"]}
AVAILABILITY_BODY: dict[str, Any] = {
    "windows": [{"weekday": 2, "start_time": "09:00", "end_time": "10:00"}],
    "blocks": [{"date": "2031-01-01"}],
}
DATE_BODY: dict[str, Any] = {"name": "Hijacked", "date": "2031-01-01", "kind": "custom"}

# Every id-bearing route: (method, path with {profile}/{date}/{note} filled in later, body).
ID_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/profiles/{profile}", None),
    ("PUT", "/profiles/{profile}", PROFILE_BODY),
    ("GET", "/profiles/{profile}/availability", None),
    ("PUT", "/profiles/{profile}/availability", AVAILABILITY_BODY),
    ("PATCH", "/couple/important-dates/{date}", {"name": "Hijacked"}),
    ("DELETE", "/couple/important-dates/{date}", None),
    ("PATCH", "/notes/{note}", {"text": "Hijacked"}),
    ("PATCH", "/notes/{note}", {"archived": True}),
    ("DELETE", "/notes/{note}", None),
]

# Every route of this wave, with a valid body, for the "no cookie" sweep.
ALL_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/couple", None),
    ("PATCH", "/couple", {"name": "x"}),
    ("PUT", "/couple/home-area", {"query": "Austin"}),
    ("GET", "/couple/constraints", None),
    ("PUT", "/couple/constraints", {}),
    ("GET", "/couple/important-dates", None),
    ("POST", "/couple/important-dates", DATE_BODY),
    ("GET", "/profiles", None),
    ("POST", "/notes", {"about_profile_id": str(uuid.uuid4()), "kind": "note", "text": "x"}),
    ("GET", "/notes", None),
    *ID_ROUTES,
]


@dataclass
class TwoCouples:
    """Ann's browser, and the ids of things that belong to Bob's couple."""

    ann: AsyncClient
    bob: AsyncClient
    profile: str
    date: str
    note: str

    def path(self, template: str) -> str:
        return API + template.format(profile=self.profile, date=self.date, note=self.note)


@pytest.fixture
async def two_couples(db_app: FastAPI) -> AsyncIterator[TwoCouples]:
    async with browser(db_app) as ann, browser(db_app) as bob:
        await sign_in(db_app, ann, "ann@example.com")
        await sign_in(db_app, bob, "bob@example.com")
        ids = await profile_ids(bob)
        date = await call(
            bob, "POST", f"{API}/couple/important-dates", DATE_BODY | {"name": "Bob's"}
        )
        note = await call(
            bob,
            "POST",
            f"{API}/notes",
            {"about_profile_id": ids["partner"], "kind": "note", "text": "Bob's secret"},
        )
        assert date.status_code == 201 and note.status_code == 201
        yield TwoCouples(ann, bob, ids["owner"], date.json()["id"], note.json()["id"])


@pytest.mark.parametrize(("method", "template", "body"), ID_ROUTES)
async def test_another_couples_ids_are_404(
    two_couples: TwoCouples, method: str, template: str, body: Any
) -> None:
    response = await call(two_couples.ann, method, two_couples.path(template), body)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(("method", "template", "body"), ID_ROUTES)
async def test_unknown_ids_are_404_too(
    two_couples: TwoCouples, method: str, template: str, body: Any
) -> None:
    ghost = str(uuid.uuid4())
    path = API + template.format(profile=ghost, date=ghost, note=ghost)
    assert (await call(two_couples.ann, method, path, body)).status_code == 404


async def test_the_other_couple_is_untouched_by_all_of_it(two_couples: TwoCouples) -> None:
    for method, template, body in ID_ROUTES:
        await call(two_couples.ann, method, two_couples.path(template), body)
    bob = two_couples.bob
    profile = (await bob.get(f"{API}/profiles/{two_couples.profile}")).json()
    assert profile["loves"] is None and profile["liked_categories"] == []
    avail = (await bob.get(f"{API}/profiles/{two_couples.profile}/availability")).json()
    assert avail == {"windows": [], "blocks": []}
    dates = (await bob.get(f"{API}/couple/important-dates")).json()
    assert [d["name"] for d in dates] == ["Bob's"]
    notes = (await bob.get(f"{API}/notes")).json()
    assert [(n["id"], n["text"], n["archived_at"]) for n in notes] == [
        (two_couples.note, "Bob's secret", None)
    ]
    # Ann's own space shows none of Bob's rows either.
    assert (await two_couples.ann.get(f"{API}/couple/important-dates")).json() == []
    assert (await two_couples.ann.get(f"{API}/notes")).json() == []
    ann_profiles = await profile_ids(two_couples.ann)
    assert two_couples.profile not in ann_profiles.values()


async def test_a_note_cannot_target_another_couples_profile(two_couples: TwoCouples) -> None:
    response = await call(
        two_couples.ann,
        "POST",
        f"{API}/notes",
        {"about_profile_id": two_couples.profile, "kind": "note", "text": "x"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_profile"
    date = await call(
        two_couples.ann,
        "POST",
        f"{API}/couple/important-dates",
        DATE_BODY | {"kind": "birthday", "profile_id": two_couples.profile},
    )
    assert date.status_code == 422 and date.json()["error"]["code"] == "invalid_profile"


@pytest.mark.parametrize(("method", "template", "body"), ALL_ROUTES)
async def test_every_route_needs_a_session(
    db_client: AsyncClient, method: str, template: str, body: Any
) -> None:
    ghost = str(uuid.uuid4())
    response = await call(
        db_client, method, API + template.format(profile=ghost, date=ghost, note=ghost), body
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_a_partner_cannot_read_or_change_the_owners_notes(db_app: FastAPI) -> None:
    async with joined_couple(db_app) as (owner, partner):
        ids = await profile_ids(owner)
        note = await call(
            owner,
            "POST",
            f"{API}/notes",
            {"about_profile_id": ids["partner"], "kind": "note", "text": "Surprise party"},
        )
        url = f"{API}/notes/{note.json()['id']}"
        assert (await partner.get(f"{API}/notes")).json() == []
        for method, body in (
            ("PATCH", {"text": "spoiled"}),
            ("PATCH", {"archived": True}),
            ("DELETE", None),
        ):
            assert (await call(partner, method, url, body)).status_code == 404
        assert [n["text"] for n in (await owner.get(f"{API}/notes")).json()] == ["Surprise party"]

        # The same holds the other way round.
        hers = await call(
            partner,
            "POST",
            f"{API}/notes",
            {"about_profile_id": ids["owner"], "kind": "note", "text": "Gift idea"},
        )
        assert [n["text"] for n in (await owner.get(f"{API}/notes")).json()] == ["Surprise party"]
        assert (await call(owner, "DELETE", f"{API}/notes/{hers.json()['id']}")).status_code == 404


async def test_edit_rights_follow_the_subject_not_the_role(db_app: FastAPI) -> None:
    async with browser(db_app) as owner:
        await sign_in(db_app, owner, "ann@example.com")
        ids = await profile_ids(owner)
        # Before she joins, the owner may fill in her profile and availability.
        url = f"{API}/profiles/{ids['partner']}"
        assert (await call(owner, "PUT", url, PROFILE_BODY)).status_code == 200
        assert (
            await call(owner, "PUT", f"{url}/availability", AVAILABILITY_BODY)
        ).status_code == 200

    async with joined_couple(db_app, "cat@example.com", "dan@example.com") as (cat, dan):
        ids = await profile_ids(cat)
        url = f"{API}/profiles/{ids['partner']}"
        for suffix, body in (("", PROFILE_BODY), ("/availability", AVAILABILITY_BODY)):
            denied = await call(cat, "PUT", url + suffix, body)
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "forbidden"
            assert (await call(dan, "PUT", url + suffix, body)).status_code == 200


async def test_profile_writes_lock_the_row_before_checking_who_may_edit(db_app: FastAPI) -> None:
    """A partner joining sets subject_user_id on the profile row; the owner's write must hold
    FOR UPDATE on it, so the join cannot land between the 403 check and the write."""
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db_app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        async with browser(db_app) as owner:
            await sign_in(db_app, owner, "ann@example.com")
            url = f"{API}/profiles/{(await profile_ids(owner))['partner']}"
            for suffix, body in (("", PROFILE_BODY), ("/availability", AVAILABILITY_BODY)):
                statements.clear()
                assert (await call(owner, "PUT", url + suffix, body)).status_code == 200
                assert any(
                    "FROM partner_profiles" in s and s.rstrip().endswith("FOR UPDATE")
                    for s in statements
                ), suffix
    finally:
        event.remove(engine, "before_cursor_execute", record)

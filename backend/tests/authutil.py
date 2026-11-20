"""Helpers for the auth tests: sign users in without going through email."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select, update

from app.models import CoupleMember, PartnerProfile, User
from app.services.auth import issue_magic_link

BASE_URL = "http://testserver"


def token_of(link: str) -> str:
    """The token in a magic link's fragment."""
    assert "#token=" in link
    return link.split("#token=", 1)[1]


async def new_link(app: FastAPI, email: str) -> str:
    """A magic link for `email`, issued straight through the service (no HTTP, no limits)."""
    async with app.state.sessionmaker() as session:
        link: str = await issue_magic_link(session, app.state.settings, email)
        await session.commit()
    return link


def csrf(client: AsyncClient) -> dict[str, str]:
    """The header that matches the client's current csrf_token cookie."""
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


async def post(client: AsyncClient, url: str, **kwargs: Any) -> Response:
    """POST with a CSRF header taken from the client's cookie (fetched first if missing)."""
    if "csrf_token" not in client.cookies:
        await client.get("/api/v1/meta")
    headers = {**csrf(client), **kwargs.pop("headers", {})}
    return await client.post(url, headers=headers, **kwargs)


async def patch(client: AsyncClient, url: str, **kwargs: Any) -> Response:
    if "csrf_token" not in client.cookies:
        await client.get("/api/v1/meta")
    headers = {**csrf(client), **kwargs.pop("headers", {})}
    return await client.patch(url, headers=headers, **kwargs)


@asynccontextmanager
async def browser(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A fresh client with its own cookie jar, like another browser."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


async def sign_in(
    app: FastAPI, client: AsyncClient, email: str, timezone: str | None = None
) -> Response:
    """Sign `client` in as `email`; asserts success."""
    token = token_of(await new_link(app, email))
    body: dict[str, Any] = {"token": token}
    if timezone is not None:
        body["timezone"] = timezone
    response = await post(client, "/api/v1/auth/verify", json=body)
    assert response.status_code == 200, response.text
    return response


async def call(client: AsyncClient, method: str, url: str, json: Any = None) -> Response:
    """Any method; the unsafe ones carry a CSRF header taken from the client's cookie."""
    if method == "GET":
        return await client.get(url)
    if "csrf_token" not in client.cookies:
        await client.get("/api/v1/meta")
    return await client.request(method, url, headers=csrf(client), json=json)


async def add_partner(app: FastAPI, owner_email: str, partner_email: str) -> None:
    """Put a second member into `owner_email`'s couple: the partner user, their membership
    and the claim on the partner slot, as when the partner joins."""
    async with app.state.sessionmaker() as session:
        couple_id = (
            await session.execute(
                select(CoupleMember.couple_id)
                .join(User, User.id == CoupleMember.user_id)
                .where(User.email == owner_email, CoupleMember.left_at.is_(None))
            )
        ).scalar_one()
        partner = User(email=partner_email, display_name="Partner")
        session.add(partner)
        await session.flush()
        session.add(CoupleMember(couple_id=couple_id, user_id=partner.id, role="partner"))
        await session.execute(
            update(PartnerProfile)
            .where(PartnerProfile.couple_id == couple_id, PartnerProfile.slot == "partner")
            .values(subject_user_id=partner.id)
        )
        await session.commit()


@asynccontextmanager
async def joined_couple(
    app: FastAPI, owner_email: str = "ann@example.com", partner_email: str = "bea@example.com"
) -> AsyncIterator[tuple[AsyncClient, AsyncClient]]:
    """Two signed-in browsers (owner, joined partner) in one couple."""
    async with browser(app) as owner, browser(app) as partner:
        await sign_in(app, owner, owner_email)
        await add_partner(app, owner_email, partner_email)
        await sign_in(app, partner, partner_email)
        yield owner, partner


async def profile_ids(client: AsyncClient) -> dict[str, str]:
    """slot -> profile id of the caller's couple."""
    response = await client.get("/api/v1/profiles")
    assert response.status_code == 200, response.text
    return {p["slot"]: p["id"] for p in response.json()}

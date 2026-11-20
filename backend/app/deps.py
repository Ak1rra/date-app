"""Shared FastAPI dependencies."""

from typing import NoReturn, cast

from fastapi import Request

from app.config import Settings
from app.errors import ApiError


def get_app_settings(request: Request) -> Settings:
    """The Settings the app was built with (tests build apps with their own)."""
    return cast(Settings, request.app.state.settings)


async def current_user() -> NoReturn:
    """Placeholder, filled in by the auth task: resolve the session cookie to a user.

    Until then no request is authenticated, so this always answers 401.
    """
    raise ApiError(401, "unauthenticated", "Please sign in to continue.")

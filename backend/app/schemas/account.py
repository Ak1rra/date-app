"""Account export and deletion response models (FR-AUTH-3, NFR-PRIV-2)."""

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel


class ExportUser(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    timezone: str
    notification_settings: dict[str, Any]
    created_at: dt.datetime


class ExportProfile(BaseModel):
    """The caller's own profile row, with its availability and important dates."""

    profile: dict[str, Any]
    availability_windows: list[dict[str, Any]]
    availability_blocks: list[dict[str, Any]]
    important_dates: list[dict[str, Any]]


class AccountExportOut(BaseModel):
    """Everything the caller owns, and nothing of their partner's private data."""

    exported_at: dt.datetime
    user: ExportUser
    profile: ExportProfile | None
    planner_notes: list[dict[str, Any]]
    ideas: list[dict[str, Any]]
    votes: list[dict[str, Any]]
    ratings: list[dict[str, Any]]
    planner_reads: list[dict[str, Any]]
    chat_messages: list[dict[str, Any]]
    notifications: list[dict[str, Any]]


class AccountDeleteOut(BaseModel):
    """`reset` in NO_LOGIN mode (the household user stays); `deleted` in email mode."""

    mode: Literal["reset", "deleted"]

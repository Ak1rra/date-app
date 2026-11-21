"""Notification centre, notification settings and Web Push subscription routes."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from pydantic import AnyHttpUrl, TypeAdapter
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.deps import CurrentUser, current_user, get_app_settings
from app.errors import ApiError
from app.models import Notification, PushSubscription
from app.schemas.notify import (
    NotificationOut,
    NotificationSettings,
    NotificationSettingsPatch,
    OkOut,
    PushConfigOut,
    PushSubscriptionIn,
    UnreadCountOut,
    UpdatedOut,
)
from app.services.notify import effective_settings

router = APIRouter(tags=["notifications"])
_HTTP_URL = TypeAdapter(AnyHttpUrl)


def _not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found.")


def _utcnow() -> datetime:
    return datetime.now(UTC)


@router.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    unread: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> list[NotificationOut]:
    query = select(Notification).where(
        Notification.user_id == current.user.id,
        Notification.deliver_after <= func.now(),
    )
    if unread:
        query = query.where(Notification.read_at.is_(None))
    rows = await session.execute(query.order_by(Notification.created_at.desc()).limit(limit))
    return [NotificationOut.model_validate(row) for row in rows.scalars().all()]


@router.get("/notifications/unread-count", response_model=UnreadCountOut)
async def unread_count(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> UnreadCountOut:
    count = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == current.user.id,
                Notification.deliver_after <= func.now(),
                Notification.read_at.is_(None),
            )
        )
    ).scalar_one()
    return UnreadCountOut(count=int(count))


@router.post("/notifications/read-all", response_model=UpdatedOut)
async def read_all_notifications(
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> UpdatedOut:
    result = await session.execute(
        update(Notification)
        .where(
            Notification.user_id == current.user.id,
            Notification.deliver_after <= func.now(),
            Notification.read_at.is_(None),
        )
        .values(read_at=_utcnow())
    )
    await session.commit()
    return UpdatedOut(updated=int(result.rowcount))  # type: ignore[attr-defined]


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> NotificationOut:
    row = (
        await session.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == current.user.id,
                Notification.deliver_after <= func.now(),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise _not_found()
    if row.read_at is None:
        row.read_at = _utcnow()
        await session.commit()
    return NotificationOut.model_validate(row)


@router.get("/notifications/push-config", response_model=PushConfigOut)
async def push_config(
    settings: Settings = Depends(get_app_settings),
    _: CurrentUser = Depends(current_user),
) -> PushConfigOut:
    return PushConfigOut(public_key=settings.vapid_public_key)


@router.get("/me/notification-settings", response_model=NotificationSettings)
async def get_notification_settings(
    current: CurrentUser = Depends(current_user),
) -> NotificationSettings:
    return effective_settings(current.user.notification_settings or {})


@router.put("/me/notification-settings", response_model=NotificationSettings)
async def put_notification_settings(
    body: NotificationSettingsPatch,
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> NotificationSettings:
    raw: dict[str, Any] = dict(current.user.notification_settings or {})
    if body.kinds is not None:
        raw["kinds"] = body.kinds
    if body.quiet_start is not None:
        raw["quiet_start"] = body.quiet_start
    if body.quiet_end is not None:
        raw["quiet_end"] = body.quiet_end
    effective = effective_settings(raw)
    current.user.notification_settings = effective.model_dump()
    await session.commit()
    return effective


@router.post("/me/push-subscriptions", response_model=OkOut, status_code=201)
async def add_push_subscription(
    body: PushSubscriptionIn,
    user_agent: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> OkOut:
    statement = (
        pg_insert(PushSubscription)
        .values(
            user_id=current.user.id,
            endpoint=body.endpoint,
            p256dh=body.keys.p256dh,
            auth=body.keys.auth,
            user_agent=(user_agent or "")[:300] or None,
        )
        .on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_={
                "user_id": current.user.id,
                "p256dh": body.keys.p256dh,
                "auth": body.keys.auth,
                "user_agent": (user_agent or "")[:300] or None,
            },
        )
    )
    await session.execute(statement)
    await session.commit()
    return OkOut(ok=True)


@router.delete("/me/push-subscriptions", status_code=204)
async def delete_push_subscription(
    endpoint: Annotated[str, Query(min_length=1, max_length=2000)],
    session: AsyncSession = Depends(get_session),
    current: CurrentUser = Depends(current_user),
) -> None:
    endpoint_value = endpoint
    if not endpoint_value.startswith("https://"):
        raise ApiError(422, "validation_error", "Some of the details aren't valid.")
    try:
        _HTTP_URL.validate_python(endpoint_value)
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "validation_error", "Some of the details aren't valid.") from exc
    await session.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == current.user.id,
            PushSubscription.endpoint == endpoint_value,
        )
    )
    await session.commit()

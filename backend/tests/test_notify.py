"""Notification scheduling and VAPID command tests."""

import base64
import datetime as dt
import json

import pytest
from click.testing import CliRunner
from py_vapid import Vapid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import cli
from app.models import Job, Notification, PushSubscription, User
from app.services.notify import (
    KINDS,
    deliver_after_for,
    effective_settings,
    notify,
)
from app.worker import handlers
from tests.conftest import make_settings


def test_effective_settings_fill_and_filter() -> None:
    settings = effective_settings(
        {"kinds": {"match": False, "not-a-kind": False}, "quiet_start": "13:00"}
    )
    assert set(settings.kinds) == set(KINDS)
    assert settings.kinds["match"] is False
    assert settings.kinds["rate_prompt"] is True
    assert settings.quiet_start == "13:00"
    assert settings.quiet_end == "08:00"


@pytest.mark.parametrize(
    ("start", "end", "moment", "expected"),
    [
        (
            dt.time(22),
            dt.time(8),
            dt.datetime(2026, 1, 2, 23, tzinfo=dt.UTC),
            "2026-01-03T08:00:00+00:00",
        ),
        (
            dt.time(13),
            dt.time(14),
            dt.datetime(2026, 1, 2, 13, 30, tzinfo=dt.UTC),
            "2026-01-02T14:00:00+00:00",
        ),
    ],
)
def test_deliver_after_for_windows(
    start: dt.time, end: dt.time, moment: dt.datetime, expected: str
) -> None:
    assert deliver_after_for(moment, "UTC", start, end).isoformat() == expected


def test_deliver_after_equal_times_disables_quiet_hours() -> None:
    moment = dt.datetime(2026, 1, 2, 13, 30, tzinfo=dt.UTC)
    assert deliver_after_for(moment, "America/New_York", dt.time(13), dt.time(13)) == moment


def test_deliver_after_fall_back_repeated_end_hour() -> None:
    # 2026-11-01 01:35 EST (second 01:35, after the 02:00 EDT -> 01:00 EST fall-back).
    moment = dt.datetime(2026, 11, 1, 6, 35, tzinfo=dt.UTC)
    result = deliver_after_for(moment, "America/New_York", dt.time(22), dt.time(1, 45))
    assert result == dt.datetime(2026, 11, 1, 6, 45, tzinfo=dt.UTC)


def test_deliver_after_dst_transition() -> None:
    moment = dt.datetime(2026, 3, 8, 6, 30, tzinfo=dt.UTC)  # 01:30 before spring forward
    result = deliver_after_for(moment, "America/New_York", dt.time(22), dt.time(8))
    assert result == dt.datetime(2026, 3, 8, 12, tzinfo=dt.UTC)


@pytest.mark.db
async def test_notify_dedupes_and_disabled_kind_keeps_in_app(
    db_session: AsyncSession,
) -> None:
    user = User(
        email="notify@example.com",
        timezone="UTC",
        notification_settings={"kinds": {"match": False}},
    )
    db_session.add(user)
    await db_session.flush()
    subscription = PushSubscription(
        user_id=user.id,
        endpoint="https://push.example.test/a",
        p256dh="p256dh",
        auth="auth",
    )
    db_session.add(subscription)
    await db_session.commit()
    settings = make_settings(vapid_public_key="public", vapid_private_key="private")

    first = await notify(
        db_session,
        user.id,
        "match",
        "Match",
        "You matched.",
        dedupe_key="match:1",
        settings=settings,
    )
    duplicate = await notify(
        db_session,
        user.id,
        "match",
        "Match",
        "You matched.",
        dedupe_key="match:1",
        settings=settings,
    )
    await db_session.commit()
    assert first is not None
    assert duplicate is None
    assert len((await db_session.execute(select(Notification))).scalars().all()) == 1
    assert len((await db_session.execute(select(Job))).scalars().all()) == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/notifications/123", "/notifications/123"),
        ("https://evil.example/x", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("/ok\npath", "/"),
        ("/ok\x85path", "/"),
        ("/‮evil", "/"),
        (123, "/"),
        ("/" + "x" * 300, "/"),
    ],
)
def test_push_url_is_sanitized(value: object, expected: str) -> None:
    assert handlers._push_url({"url": value}) == expected


@pytest.mark.db
async def test_notify_enqueues_before_a_subscription_is_created(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(email="late-sub@example.com", timezone="UTC")
    db_session.add(user)
    await db_session.flush()
    settings = make_settings(vapid_public_key="public", vapid_private_key="private")
    notification = await notify(
        db_session,
        user.id,
        "match",
        "Match",
        "You matched.",
        respect_quiet_hours=False,
        settings=settings,
    )
    assert notification is not None
    db_session.add(
        PushSubscription(
            user_id=user.id,
            endpoint="https://push.example.test/late",
            p256dh="p256dh",
            auth="auth",
        )
    )
    await db_session.commit()
    sent: list[str] = []

    def fake_webpush(**kwargs: object) -> None:
        sent.append(str(kwargs["data"]))

    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(handlers, "webpush", fake_webpush)
    await handlers.push_send(db_session, {"notification_id": str(notification.id)})
    await db_session.commit()
    assert len(sent) == 1
    assert json.loads(sent[0])["title"] == "Match"
    stored = await db_session.get(Notification, notification.id)
    assert stored is not None and stored.pushed_at is not None


@pytest.mark.db
async def test_notify_without_subscriptions_enqueues_and_marks_pushed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(email="no-sub@example.com", timezone="UTC")
    db_session.add(user)
    await db_session.flush()
    settings = make_settings(vapid_public_key="public", vapid_private_key="private")
    notification = await notify(
        db_session,
        user.id,
        "match",
        "Match",
        "You matched.",
        respect_quiet_hours=False,
        settings=settings,
    )
    assert notification is not None
    await db_session.commit()
    jobs = (await db_session.execute(select(Job))).scalars().all()
    assert len(jobs) == 1 and jobs[0].kind == "push.send"
    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    await handlers.push_send(db_session, {"notification_id": str(notification.id)})
    await db_session.commit()
    stored = await db_session.get(Notification, notification.id)
    assert stored is not None and stored.pushed_at is not None


@pytest.mark.db
async def test_push_send_retries_when_every_delivery_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(email="push-fail@example.com", timezone="UTC")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        PushSubscription(
            user_id=user.id,
            endpoint="https://push.example.test/fail",
            p256dh="p256dh",
            auth="auth",
        )
    )
    notification = Notification(
        user_id=user.id,
        kind="match",
        title="Match",
        body="You matched.",
        deliver_after=dt.datetime.now(dt.UTC),
    )
    db_session.add(notification)
    await db_session.commit()
    settings = make_settings(vapid_public_key="public", vapid_private_key="private")

    def fail_webpush(**_: object) -> None:
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(handlers, "webpush", fail_webpush)
    notification_id = notification.id
    with pytest.raises(RuntimeError, match="all push deliveries failed"):
        await handlers.push_send(db_session, {"notification_id": str(notification_id)})
    await db_session.rollback()
    stored = await db_session.get(Notification, notification_id)
    assert stored is not None and stored.pushed_at is None


@pytest.mark.db
async def test_push_send_marks_pushed_after_partial_success(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(email="push-partial@example.com", timezone="UTC")
    db_session.add(user)
    await db_session.flush()
    db_session.add_all(
        [
            PushSubscription(
                user_id=user.id,
                endpoint="https://push.example.test/fail",
                p256dh="p256dh",
                auth="auth",
            ),
            PushSubscription(
                user_id=user.id,
                endpoint="https://push.example.test/succeed",
                p256dh="p256dh",
                auth="auth",
            ),
        ]
    )
    notification = Notification(
        user_id=user.id,
        kind="match",
        title="Match",
        body="You matched.",
        deliver_after=dt.datetime.now(dt.UTC),
    )
    db_session.add(notification)
    await db_session.commit()
    settings = make_settings(vapid_public_key="public", vapid_private_key="private")

    def mixed_webpush(**kwargs: object) -> None:
        if str(kwargs["subscription_info"]).find("/fail") >= 0:
            raise RuntimeError("temporary failure")

    monkeypatch.setattr(handlers, "get_settings", lambda: settings)
    monkeypatch.setattr(handlers, "webpush", mixed_webpush)
    await handlers.push_send(db_session, {"notification_id": str(notification.id)})
    await db_session.commit()
    stored = await db_session.get(Notification, notification.id)
    assert stored is not None and stored.pushed_at is not None


def test_vapid_generate_outputs_parseable_keys() -> None:
    result = CliRunner().invoke(cli, ["vapid", "generate"])
    assert result.exit_code == 0, result.output
    values = dict(line.split("=", 1) for line in result.output.strip().splitlines())
    public = base64.urlsafe_b64decode(values["VAPID_PUBLIC_KEY"] + "==")
    private = base64.urlsafe_b64decode(values["VAPID_PRIVATE_KEY"] + "==")
    assert len(public) == 65 and public[0] == 4
    assert len(private) == 32
    vapid = Vapid.from_string(values["VAPID_PRIVATE_KEY"])
    assert vapid.sign({"sub": "mailto:admin@dates.lan", "aud": "https://push.example.test"})

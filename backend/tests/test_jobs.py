"""The Postgres job runner and auth.cleanup against the real database (marker: db)."""

import asyncio
import datetime as dt
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Job, MagicLinkToken, User, UserSession
from app.worker import handlers, jobs, runner
from app.worker.jobs import claim_due, enqueue, run_batch, run_job
from tests.conftest import make_settings

pytestmark = pytest.mark.db

SessionMaker = async_sessionmaker[AsyncSession]
WORKER = "test-host:1"


@pytest.fixture
async def sessionmaker(db_schema: str) -> AsyncIterator[SessionMaker]:
    engine = create_async_engine(db_schema)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def add_handler(monkeypatch: pytest.MonkeyPatch, kind: str, fn: Any) -> None:
    monkeypatch.setitem(jobs._REGISTRY, kind, fn)


async def enqueue_committed(
    sm: SessionMaker, kind: str, payload: dict[str, Any], **kw: Any
) -> bool:
    async with sm() as session:
        inserted = await enqueue(session, kind, payload, **kw)
        await session.commit()
    return inserted


async def get_job(sm: SessionMaker, kind: str) -> Job:
    async with sm() as session:
        return (await session.execute(select(Job).where(Job.kind == kind))).scalar_one()


async def sql(sm: SessionMaker, statement: str, **params: Any) -> None:
    async with sm() as session:
        await session.execute(text(statement), params)
        await session.commit()


async def count_jobs(sm: SessionMaker, **where: Any) -> int:
    async with sm() as session:
        stmt = select(func.count()).select_from(Job)
        for column, value in where.items():
            stmt = stmt.where(getattr(Job, column) == value)
        return (await session.execute(stmt)).scalar_one()


# --- enqueue


async def test_enqueue_dedupes(sessionmaker: SessionMaker) -> None:
    assert await enqueue_committed(sessionmaker, "t.kind", {"n": 1}, dedupe_key="k1") is True
    assert await enqueue_committed(sessionmaker, "t.kind", {"n": 2}, dedupe_key="k1") is False
    assert await enqueue_committed(sessionmaker, "t.kind", {"n": 3}, dedupe_key="k2") is True
    # Without a key nothing dedupes (NULLs are distinct).
    assert await enqueue_committed(sessionmaker, "t.kind", {}) is True
    assert await enqueue_committed(sessionmaker, "t.kind", {}) is True
    assert await count_jobs(sessionmaker) == 4
    async with sessionmaker() as session:
        first = (await session.execute(select(Job).where(Job.dedupe_key == "k1"))).scalar_one()
    assert first.payload == {"n": 1}  # the loser did not overwrite it
    assert (first.status, first.attempts, first.max_attempts) == ("queued", 0, 5)


async def test_enqueue_sets_run_at(sessionmaker: SessionMaker) -> None:
    when = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
    await enqueue_committed(sessionmaker, "t.later", {}, run_at=when)
    assert (await get_job(sessionmaker, "t.later")).run_at == when


# --- claim and run


async def test_due_job_runs_and_is_done(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    async def handler(session: AsyncSession, payload: dict[str, Any]) -> None:
        seen.append(payload)

    add_handler(monkeypatch, "t.ok", handler)
    await enqueue_committed(sessionmaker, "t.ok", {"a": 1})
    later = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
    await enqueue_committed(sessionmaker, "t.ok", {"a": 2}, run_at=later)

    assert await run_batch(sessionmaker, WORKER) == 1
    assert seen == [{"a": 1}]
    async with sessionmaker() as session:
        done = (await session.execute(select(Job).where(Job.status == "done"))).scalar_one()
        queued = (await session.execute(select(Job).where(Job.status == "queued"))).scalar_one()
    assert done.payload == {"a": 1}
    assert done.finished_at is not None
    assert (done.locked_at, done.locked_by) == (None, None)
    assert queued.payload == {"a": 2}  # not due, untouched
    assert await run_batch(sessionmaker, WORKER) == 0


async def test_claim_marks_jobs_running_and_respects_limit(sessionmaker: SessionMaker) -> None:
    for n in range(5):
        await enqueue_committed(sessionmaker, "t.k", {"n": n})
    async with sessionmaker() as session:
        claimed = await claim_due(session, WORKER, limit=3)
    assert len(claimed) == 3
    assert await count_jobs(sessionmaker, status="running", locked_by=WORKER) == 3
    assert await count_jobs(sessionmaker, status="queued") == 2


async def test_failing_handler_is_requeued_with_backoff_then_failed(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(session: AsyncSession, payload: dict[str, Any]) -> None:
        raise RuntimeError("it broke " + "x" * 5000)

    add_handler(monkeypatch, "t.boom", boom)
    await enqueue_committed(sessionmaker, "t.boom", {"secret": "hunter2"})
    await sql(sessionmaker, "UPDATE jobs SET max_attempts = 3")

    async def due_in_seconds() -> float:
        async with sessionmaker() as session:
            stmt = select(func.extract("epoch", Job.run_at - func.now()))
            return float((await session.execute(stmt)).scalar_one())

    with caplog.at_level(logging.ERROR, logger="app.worker"):
        for attempt, delay in ((1, 30), (2, 60)):
            assert await run_batch(sessionmaker, WORKER) == 1
            job = await get_job(sessionmaker, "t.boom")
            assert (job.status, job.attempts) == ("queued", attempt)
            assert (job.locked_at, job.locked_by, job.finished_at) == (None, None, None)
            assert job.last_error is not None and job.last_error.startswith(
                "RuntimeError: it broke"
            )
            assert len(job.last_error) == 2000
            assert delay - 5 < await due_in_seconds() <= delay
            assert await run_batch(sessionmaker, WORKER) == 0  # backing off: not due yet
            await sql(sessionmaker, "UPDATE jobs SET run_at = now() - interval '1 second'")
        assert await run_batch(sessionmaker, WORKER) == 1  # third failure: attempts == max
    job = await get_job(sessionmaker, "t.boom")
    assert (job.status, job.attempts) == ("failed", 3)
    assert job.finished_at is not None
    assert job.last_error is not None and "it broke" in job.last_error
    assert await run_batch(sessionmaker, WORKER) == 0
    # Logged with the job id and kind, never the payload.
    records = [r for r in caplog.records if r.getMessage() == "job failed"]
    assert len(records) == 3
    assert {r.kind for r in records} == {"t.boom"}  # type: ignore[attr-defined]
    assert str(job.id) in {r.job_id for r in records}  # type: ignore[attr-defined]
    assert "hunter2" not in caplog.text


async def test_handler_errors_never_put_the_payload_in_the_logs(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def leaky(session: AsyncSession, payload: dict[str, Any]) -> None:
        raise RuntimeError(f"boom {payload['secret']}")

    add_handler(monkeypatch, "t.leaky", leaky)
    await enqueue_committed(sessionmaker, "t.leaky", {"secret": "s3cr3t-value"})
    with caplog.at_level(logging.INFO, logger="app.worker"):
        await run_batch(sessionmaker, WORKER)
    for record in caplog.records:
        assert "s3cr3t-value" not in record.getMessage()
        assert record.exc_info is None and record.exc_text is None  # no traceback at >= INFO
    (failure,) = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert failure.error_type == "RuntimeError"  # type: ignore[attr-defined]
    # The database keeps the text for the operator; that is not a log.
    assert "boom" in (await get_job(sessionmaker, "t.leaky")).last_error  # type: ignore[operator]


async def test_failing_handler_rolls_back_its_own_writes(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def half_done(session: AsyncSession, payload: dict[str, Any]) -> None:
        session.add(User(email="ghost@example.com"))
        await session.flush()
        raise RuntimeError("after the write")

    add_handler(monkeypatch, "t.half", half_done)
    await enqueue_committed(sessionmaker, "t.half", {})
    await run_batch(sessionmaker, WORKER)
    async with sessionmaker() as session:
        assert (await session.execute(select(func.count()).select_from(User))).scalar_one() == 0


async def test_unknown_kind_fails(sessionmaker: SessionMaker) -> None:
    await enqueue_committed(sessionmaker, "t.nobody_home", {})
    assert await run_batch(sessionmaker, WORKER) == 1
    job = await get_job(sessionmaker, "t.nobody_home")
    assert job.status == "failed"
    assert job.last_error is not None and "t.nobody_home" in job.last_error
    assert job.finished_at is not None
    assert await run_batch(sessionmaker, WORKER) == 0


async def test_stuck_running_job_is_reclaimed_after_ten_minutes(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    async def handler(session: AsyncSession, payload: dict[str, Any]) -> None:
        ran.append(payload["name"])

    add_handler(monkeypatch, "t.stuck", handler)
    await enqueue_committed(sessionmaker, "t.stuck", {"name": "stale"})
    await enqueue_committed(sessionmaker, "t.stuck", {"name": "fresh"})
    await sql(
        sessionmaker,
        "UPDATE jobs SET status = 'running', locked_by = 'dead:1', "
        "locked_at = now() - (CASE WHEN payload->>'name' = 'stale' THEN 11 ELSE 9 END) "
        "* interval '1 minute'",
    )
    assert await run_batch(sessionmaker, WORKER) == 1
    assert ran == ["stale"]
    assert await count_jobs(sessionmaker, status="done") == 1
    async with sessionmaker() as session:
        done = (await session.execute(select(Job).where(Job.status == "done"))).scalar_one()
    assert done.attempts == 1  # the reclaim counted as an attempt
    assert await count_jobs(sessionmaker, status="running") == 1  # the 9-minute one is left alone


async def test_reclaim_counts_as_an_attempt_and_the_last_one_fails_the_job(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    async def handler(session: AsyncSession, payload: dict[str, Any]) -> None:
        ran.append(payload["name"])

    add_handler(monkeypatch, "t.dies", handler)
    await enqueue_committed(sessionmaker, "t.dies", {"name": "some-left"})
    await enqueue_committed(sessionmaker, "t.dies", {"name": "last-one"})
    await sql(
        sessionmaker,
        "UPDATE jobs SET status = 'running', locked_by = 'dead:1', "
        "locked_at = now() - interval '11 minutes', "
        "attempts = CASE WHEN payload->>'name' = 'last-one' THEN max_attempts - 1 ELSE 2 END",
    )
    async with sessionmaker() as session:
        claimed = await claim_due(session, WORKER)
    assert [(c.payload["name"], c.attempts) for c in claimed] == [("some-left", 3)]
    async with sessionmaker() as session:
        rows = {j.payload["name"]: j for j in (await session.execute(select(Job))).scalars()}
    assert (rows["some-left"].status, rows["some-left"].attempts) == ("running", 3)
    assert rows["some-left"].locked_by == WORKER
    last = rows["last-one"]
    assert (last.status, last.attempts) == ("failed", 5)
    assert last.last_error == "worker died while running this job (reclaimed 5 times)"
    assert last.finished_at is not None
    assert (last.locked_at, last.locked_by) == (None, None)
    assert ran == []  # neither ran: claim_due only claims


async def test_a_job_on_its_last_attempt_is_failed_not_run_when_reclaimed(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    async def handler(session: AsyncSession, payload: dict[str, Any]) -> None:
        ran.append("ran")

    add_handler(monkeypatch, "t.poison", handler)
    await enqueue_committed(sessionmaker, "t.poison", {})
    await sql(
        sessionmaker,
        "UPDATE jobs SET status = 'running', locked_by = 'dead:1', attempts = max_attempts - 1, "
        "locked_at = now() - interval '11 minutes'",
    )
    assert await run_batch(sessionmaker, WORKER) == 0
    assert ran == []
    job = await get_job(sessionmaker, "t.poison")
    assert (job.status, job.attempts) == ("failed", 5)
    assert await run_batch(sessionmaker, WORKER) == 0  # and it stays failed


async def _stale_reclaim_setup(
    sm: SessionMaker, kind: str
) -> tuple[jobs.ClaimedJob, jobs.ClaimedJob]:
    """Worker A claims the job, then its lock goes stale and worker B reclaims it."""
    await enqueue_committed(sm, kind, {})
    async with sm() as session:
        (claimed_by_a,) = await claim_due(session, "A:1")
    await sql(sm, "UPDATE jobs SET locked_at = now() - interval '11 minutes'")
    async with sm() as session:
        (claimed_by_b,) = await claim_due(session, "B:2")
    assert (claimed_by_b.locked_by, claimed_by_b.attempts) == ("B:2", 1)
    return claimed_by_a, claimed_by_b


async def test_a_worker_that_lost_its_claim_cannot_mark_the_job_done(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(session: AsyncSession, payload: dict[str, Any]) -> None:
        session.add(User(email="from-a@example.com"))
        await session.flush()
        started.set()
        await release.wait()

    add_handler(monkeypatch, "t.fenced", slow)
    await enqueue_committed(sessionmaker, "t.fenced", {})
    async with sessionmaker() as session:
        (claimed_by_a,) = await claim_due(session, "A:1")
    with caplog.at_level(logging.WARNING, logger="app.worker"):
        run_a = asyncio.create_task(run_job(sessionmaker, claimed_by_a))
        await asyncio.wait_for(started.wait(), timeout=5)
        await sql(sessionmaker, "UPDATE jobs SET locked_at = now() - interval '11 minutes'")
        async with sessionmaker() as session:
            (claimed_by_b,) = await claim_due(session, "B:2")
        release.set()
        await asyncio.wait_for(run_a, timeout=5)
    job = await get_job(sessionmaker, "t.fenced")
    assert (job.status, job.locked_by, job.attempts) == ("running", "B:2", 1)
    assert job.locked_at is not None and job.locked_at == claimed_by_b.locked_at
    async with sessionmaker() as session:
        # A's handler write went down with its transaction.
        assert (await session.execute(select(func.count()).select_from(User))).scalar_one() == 0
    lost = [r for r in caplog.records if "reclaimed by another worker" in r.getMessage()]
    assert len(lost) == 1 and lost[0].kind == "t.fenced"  # type: ignore[attr-defined]


async def test_a_worker_that_lost_its_claim_cannot_requeue_or_fail_the_job(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_then_raise(session: AsyncSession, payload: dict[str, Any]) -> None:
        started.set()
        await release.wait()
        raise RuntimeError("late failure")

    add_handler(monkeypatch, "t.fenced", slow_then_raise)
    await enqueue_committed(sessionmaker, "t.fenced", {})
    async with sessionmaker() as session:
        (claimed_by_a,) = await claim_due(session, "A:1")
    run_a = asyncio.create_task(run_job(sessionmaker, claimed_by_a))
    await asyncio.wait_for(started.wait(), timeout=5)
    await sql(sessionmaker, "UPDATE jobs SET locked_at = now() - interval '11 minutes'")
    async with sessionmaker() as session:
        await claim_due(session, "B:2")
    before = await get_job(sessionmaker, "t.fenced")
    release.set()
    await asyncio.wait_for(run_a, timeout=5)
    after = await get_job(sessionmaker, "t.fenced")
    for column in ("status", "attempts", "run_at", "last_error", "locked_by", "locked_at"):
        assert getattr(after, column) == getattr(before, column), column
    assert (after.status, after.locked_by, after.attempts) == ("running", "B:2", 1)


async def test_claimers_skip_rows_another_claimer_holds(sessionmaker: SessionMaker) -> None:
    for n in range(4):
        await enqueue_committed(sessionmaker, "t.k", {"n": n})
    async with sessionmaker() as holder, sessionmaker() as other:
        # `holder` locks every due row without committing.
        await holder.execute(select(Job.id).where(Job.status == "queued").with_for_update())
        # SKIP LOCKED: the second claimer comes back empty-handed instead of waiting.
        assert await asyncio.wait_for(claim_due(other, "other:2"), timeout=5) == []
        await holder.rollback()
        assert len(await claim_due(other, "other:2")) == 4


async def test_concurrent_claimers_never_share_a_job(sessionmaker: SessionMaker) -> None:
    for n in range(40):
        await enqueue_committed(sessionmaker, "t.k", {"n": n})

    async def claim(worker: str) -> list[uuid.UUID]:
        async with sessionmaker() as session:
            return [c.id for c in await claim_due(session, worker, limit=40)]

    results = await asyncio.gather(*(claim(f"w:{i}") for i in range(4)))
    claimed = [job_id for ids in results for job_id in ids]
    assert len(claimed) == len(set(claimed)) == 40
    assert await count_jobs(sessionmaker, status="running") == 40


# --- auth.cleanup


FAR_FUTURE = dt.datetime(2099, 3, 4, 10, 30, tzinfo=dt.UTC)


async def test_auth_cleanup_deletes_only_what_it_should_and_schedules_next_hour_once(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pinned far ahead so the next-hour job it schedules is never due while this test runs.
    monkeypatch.setattr(handlers, "_utcnow", lambda: FAR_FUTURE)
    now = dt.datetime.now(dt.UTC)

    def ago(**delta: float) -> dt.datetime:
        return now - dt.timedelta(**delta)

    async with sessionmaker() as session:
        user = User(email="ann@example.com")
        session.add(user)
        await session.flush()
        for name, expires in (
            ("tok-old", ago(days=2)),  # expired over a day ago: deleted
            ("tok-recent", ago(hours=2)),  # expired, but within the day: kept
            ("tok-live", now + dt.timedelta(minutes=10)),
        ):
            session.add(MagicLinkToken(email="a@example.com", token_hash=name, expires_at=expires))
        future = now + dt.timedelta(days=30)
        for name, expires, revoked in (
            ("ses-expired-old", ago(days=8), None),  # deleted
            ("ses-expired-recent", ago(days=6), None),  # kept
            ("ses-revoked-old", future, ago(days=8)),  # deleted
            ("ses-revoked-recent", future, ago(days=6)),  # kept
            ("ses-live", future, None),  # kept
        ):
            session.add(
                UserSession(
                    user_id=user.id, token_hash=name, expires_at=expires, revoked_at=revoked
                )
            )
        await session.commit()

    # Jobs the cleanup judges by status and age. Only old done/failed ones may go; a queued or
    # running job is never touched, however old its finished_at (not due, not stale: not claimed).
    for name, status, finished, run_at, locked in (
        ("job-done-old", "done", "8 days", "now()", None),
        ("job-failed-old", "failed", "8 days", "now()", None),
        ("job-done-recent", "done", "6 days", "now()", None),
        ("job-queued", "queued", "30 days", "'2099-01-01'", None),
        ("job-running", "running", "30 days", "now()", "now()"),
    ):
        await sql(
            sessionmaker,
            "INSERT INTO jobs (id, kind, payload, status, finished_at, run_at, locked_at) "
            f"VALUES (gen_random_uuid(), 't.keep', jsonb_build_object('name', '{name}'), "
            f"'{status}', now() - interval '{finished}', {run_at}, {locked or 'NULL'})",
        )
    async with sessionmaker() as session:
        assert await handlers.schedule_auth_cleanup(session, next_hour=False) is True
        await session.commit()
    assert await run_batch(sessionmaker, WORKER) == 1

    async with sessionmaker() as session:
        tokens = set((await session.execute(select(MagicLinkToken.token_hash))).scalars())
        sessions = set((await session.execute(select(UserSession.token_hash))).scalars())
    assert tokens == {"tok-recent", "tok-live"}
    assert sessions == {"ses-expired-recent", "ses-revoked-recent", "ses-live"}

    async with sessionmaker() as session:
        kept = set(
            (
                await session.execute(
                    select(Job.payload["name"].as_string()).where(Job.kind == "t.keep")
                )
            ).scalars()
        )
    assert kept == {"job-done-recent", "job-queued", "job-running"}

    current = await get_job_by_key(sessionmaker, "auth.cleanup:2099-03-04T10")
    assert current.status == "done"
    next_hour = await get_job_by_key(sessionmaker, "auth.cleanup:2099-03-04T11")
    assert next_hour.status == "queued"
    assert next_hour.run_at == dt.datetime(2099, 3, 4, 11, tzinfo=dt.UTC)

    # Running it again (a retry, a second worker) must not schedule a second next-hour job.
    async with sessionmaker() as session, session.begin():
        await handlers.auth_cleanup(session, {})
    assert await count_jobs(sessionmaker, dedupe_key="auth.cleanup:2099-03-04T11") == 1
    assert await count_jobs(sessionmaker, kind="auth.cleanup") == 2


async def get_job_by_key(sm: SessionMaker, key: str) -> Job:
    async with sm() as session:
        return (await session.execute(select(Job).where(Job.dedupe_key == key))).scalar_one()


# --- the loop


async def test_worker_enqueues_the_current_hours_cleanup_at_startup_and_runs_it(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_utcnow", lambda: FAR_FUTURE)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run_worker(make_settings(), stop, sessionmaker))
    try:
        async with asyncio.timeout(10):
            while await count_jobs(sessionmaker, status="done") < 1:  # noqa: ASYNC110
                await asyncio.sleep(0.05)  # polling the database
    finally:
        stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert (await get_job_by_key(sessionmaker, "auth.cleanup:2099-03-04T10")).status == "done"
    assert await count_jobs(sessionmaker, dedupe_key="auth.cleanup:2099-03-04T11") == 1


async def test_worker_retries_scheduling_when_the_first_attempt_fails(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "_utcnow", lambda: FAR_FUTURE)
    monkeypatch.setattr(runner, "TICK_SEC", 0.05)
    real = handlers.schedule_auth_cleanup
    calls: list[int] = []

    async def flaky(session: AsyncSession, *, next_hour: bool) -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("database not ready")
        return await real(session, next_hour=next_hour)

    monkeypatch.setattr(handlers, "schedule_auth_cleanup", flaky)
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run_worker(make_settings(), stop, sessionmaker))
    try:
        async with asyncio.timeout(10):
            while await count_jobs(sessionmaker, dedupe_key="auth.cleanup:2099-03-04T10") < 1:  # noqa: ASYNC110
                await asyncio.sleep(0.05)
    finally:
        stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert len(calls) >= 2


async def test_worker_exits_promptly_when_stopped_mid_batch(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    async def hang(session: AsyncSession, payload: dict[str, Any]) -> None:
        started.set()
        await asyncio.Event().wait()

    add_handler(monkeypatch, "t.hang", hang)
    monkeypatch.setattr(runner, "STOP_GRACE_SEC", 0.2)
    await enqueue_committed(sessionmaker, "t.hang", {})
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run_worker(make_settings(), stop, sessionmaker))
    await asyncio.wait_for(started.wait(), timeout=10)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    # The abandoned job stays `running`; it is reclaimed once its lock goes stale.
    assert (await get_job(sessionmaker, "t.hang")).status == "running"


async def test_worker_survives_a_handler_that_raises(
    sessionmaker: SessionMaker, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = asyncio.Event()

    async def boom(session: AsyncSession, payload: dict[str, Any]) -> None:
        raise RuntimeError("nope")

    async def fine(session: AsyncSession, payload: dict[str, Any]) -> None:
        ran.set()

    add_handler(monkeypatch, "t.boom", boom)
    add_handler(monkeypatch, "t.fine", fine)
    await enqueue_committed(sessionmaker, "t.boom", {})
    await enqueue_committed(sessionmaker, "t.fine", {})
    stop = asyncio.Event()
    task = asyncio.create_task(runner.run_worker(make_settings(), stop, sessionmaker))
    try:
        await asyncio.wait_for(ran.wait(), timeout=10)
        assert not task.done()
    finally:
        stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert (await get_job(sessionmaker, "t.boom")).status == "queued"

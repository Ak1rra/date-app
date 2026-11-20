"""The Postgres job queue: enqueue, claim, run (design: m1-foundation.md, "Worker").

At-least-once delivery: a job whose worker dies stays `running` until its lock is older than
STALE_LOCK and is then claimed again (counting as an attempt), so handlers must be idempotent.
A handler runs in its own session and transaction, and the transaction that marks the job `done`
is the same one the handler worked in; handlers must not commit or roll back themselves.

Payloads can hold personal data, so nothing here ever logs one.
"""

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import String, and_, case, cast, func, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.expression import ColumnElement

from app.models.jobs import Job

logger = logging.getLogger("app.worker")

Handler = Callable[[AsyncSession, dict[str, Any]], Awaitable[None]]

STALE_LOCK = timedelta(minutes=10)  # a `running` job locked longer than this is reclaimed
RETRY_BASE_SEC = 30  # retry delay is RETRY_BASE_SEC * 2**attempts_before_this_failure
JOB_TIMEOUT_SEC = 300.0  # well under STALE_LOCK, so a hung handler never overlaps its reclaim
LAST_ERROR_MAX = 2000

_NO_SYNC = {"synchronize_session": False}  # core-style updates; no ORM identity map in play
_REGISTRY: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    """Decorator: `@register("auth.cleanup")` on an `async def handler(session, payload)`.

    Handlers are our own code and must honour cancellation (the runner times them out and
    cancels them at shutdown); ownership fencing below is the backstop, not a licence to hang."""

    def decorator(handler: Handler) -> Handler:
        if kind in _REGISTRY:
            raise ValueError(f"job kind {kind!r} is already registered")
        _REGISTRY[kind] = handler
        return handler

    return decorator


def registered_kinds() -> frozenset[str]:
    return frozenset(_REGISTRY)


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    locked_by: str  # with locked_at, proves this worker still owns the claim (see _owned)
    locked_at: datetime


class ClaimLost(Exception):
    """The job was reclaimed by another worker while this one was running it."""


async def enqueue(
    session: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
) -> bool:
    """Add a job to the caller's transaction (the caller commits). Returns False, and adds
    nothing, if a job with the same `dedupe_key` already exists, whatever its status."""
    values: dict[str, Any] = {"kind": kind, "payload": payload, "dedupe_key": dedupe_key}
    if run_at is not None:
        values["run_at"] = run_at  # else the column's server default, now()
    stmt = (
        pg_insert(Job)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Job.dedupe_key])
        .returning(Job.id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def claim_due(session: AsyncSession, worker_id: str, limit: int = 10) -> list[ClaimedJob]:
    """Mark up to `limit` due jobs `running` for `worker_id`, commit, and return them.

    Due means queued with run_at <= now(), or running with a lock older than STALE_LOCK. One
    statement: FOR UPDATE SKIP LOCKED in the subquery means two claimers never get the same row.

    Reclaiming a stale `running` job counts as an attempt (its worker died mid-run). If that
    uses up max_attempts the job is failed instead of claimed, so a job that kills its worker
    cannot loop forever; it is not returned.
    """
    now = func.now()
    due = (
        select(Job.id)
        .where(
            or_(
                and_(Job.status == "queued", Job.run_at <= now),
                and_(Job.status == "running", Job.locked_at < now - STALE_LOCK),
            )
        )
        .order_by(Job.run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    reclaimed = Job.status == "running"  # the old value: every right-hand side sees the old row
    dead = and_(reclaimed, Job.attempts + 1 >= Job.max_attempts)
    died = func.concat(
        "worker died while running this job (reclaimed ",
        cast(Job.attempts + 1, String),
        " times)",
    )
    stmt = (
        update(Job)
        .where(Job.id.in_(due))
        .values(
            attempts=case((reclaimed, Job.attempts + 1), else_=Job.attempts),
            status=case((dead, "failed"), else_="running"),
            locked_at=case((dead, null()), else_=now),
            locked_by=case((dead, null()), else_=cast(worker_id, String)),
            last_error=case((dead, died), else_=Job.last_error),
            finished_at=case((dead, now), else_=Job.finished_at),
        )
        .returning(
            Job.id,
            Job.kind,
            Job.payload,
            Job.attempts,
            Job.max_attempts,
            Job.status,
            Job.locked_by,
            Job.locked_at,
        )
        .execution_options(**_NO_SYNC)
    )
    rows = (await session.execute(stmt)).all()
    await session.commit()
    for r in rows:
        if r.status == "failed":
            logger.error(
                "job abandoned: its worker died", extra={"job_id": str(r.id), "kind": r.kind}
            )
    return [
        ClaimedJob(r.id, r.kind, r.payload, r.attempts, r.max_attempts, r.locked_by, r.locked_at)
        for r in rows
        if r.status == "running"
    ]


def _owned(job: ClaimedJob) -> ColumnElement[bool]:
    """Fencing: only the worker whose claim is still current may write a job's outcome. A
    handler that outlived STALE_LOCK (hung, or ignoring cancellation) has been reclaimed, and
    its late writes must not clobber the newer claim, which has a different locked_at."""
    return and_(
        Job.id == job.id,
        Job.status == "running",
        Job.locked_by == job.locked_by,
        Job.locked_at == job.locked_at,
    )


def _log_claim_lost(job: ClaimedJob) -> None:
    logger.warning(
        "job was reclaimed by another worker; discarding this run's outcome",
        extra={"job_id": str(job.id), "kind": job.kind},
    )


async def _mark_done(session: AsyncSession, job: ClaimedJob) -> None:
    """Runs in the handler's transaction; raises ClaimLost (rolling the handler back) if the
    claim is no longer ours."""
    result = await session.execute(
        update(Job)
        .where(_owned(job))
        .values(status="done", finished_at=func.now(), locked_at=None, locked_by=None)
        .returning(Job.id)
        .execution_options(**_NO_SYNC)
    )
    if result.first() is None:
        raise ClaimLost


async def _mark_failed(
    sessionmaker: async_sessionmaker[AsyncSession], job: ClaimedJob, error: str
) -> None:
    """Re-queue with exponential backoff, or fail for good once attempts reach max_attempts."""
    attempts = job.attempts + 1
    values: dict[str, Any] = {
        "attempts": attempts,
        "last_error": error[:LAST_ERROR_MAX],
        "locked_at": None,
        "locked_by": None,
    }
    if attempts >= job.max_attempts:
        values.update(status="failed", finished_at=func.now())
    else:
        delay = timedelta(seconds=RETRY_BASE_SEC * 2**job.attempts)
        values.update(status="queued", run_at=func.now() + delay)
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            update(Job)
            .where(_owned(job))
            .values(**values)
            .returning(Job.id)
            .execution_options(**_NO_SYNC)
        )
        if result.first() is None:
            _log_claim_lost(job)


async def run_job(sessionmaker: async_sessionmaker[AsyncSession], job: ClaimedJob) -> None:
    """Run one claimed job to a terminal or re-queued state. Never raises (bar cancellation)."""
    handler = _REGISTRY.get(job.kind)
    try:
        if handler is None:
            # Not retried: a missing handler is a deploy problem, not a transient one.
            async with sessionmaker() as session, session.begin():
                result = await session.execute(
                    update(Job)
                    .where(_owned(job))
                    .values(
                        status="failed",
                        attempts=job.attempts + 1,
                        last_error=f"no handler registered for kind {job.kind!r}"[:LAST_ERROR_MAX],
                        finished_at=func.now(),
                        locked_at=None,
                        locked_by=None,
                    )
                    .returning(Job.id)
                    .execution_options(**_NO_SYNC)
                )
                if result.first() is None:
                    _log_claim_lost(job)
                    return
            logger.error("job has no handler", extra={"job_id": str(job.id), "kind": job.kind})
            return
        try:
            async with sessionmaker() as session, session.begin():
                await asyncio.wait_for(handler(session, job.payload), timeout=JOB_TIMEOUT_SEC)
                await _mark_done(session, job)
        except ClaimLost:
            _log_claim_lost(job)  # the transaction rolled back, handler writes included
        except Exception as exc:
            # No message or traceback at ERROR: either can quote the payload. last_error (in the
            # database, next to the payload) keeps the text; the traceback is DEBUG-only.
            logger.error(
                "job failed",
                extra={
                    "job_id": str(job.id),
                    "kind": job.kind,
                    "attempt": job.attempts + 1,
                    "error_type": type(exc).__name__,
                },
            )
            logger.debug(
                "job failure traceback",
                extra={"job_id": str(job.id), "kind": job.kind},
                exc_info=True,
            )
            await _mark_failed(sessionmaker, job, f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        # The database itself is failing. The job stays `running` and is reclaimed after
        # STALE_LOCK; the loop carries on. Database errors can quote statement parameters.
        logger.error(
            "could not record the outcome of a job",
            extra={"job_id": str(job.id), "kind": job.kind, "error_type": type(exc).__name__},
        )
        logger.debug(
            "outcome failure traceback",
            extra={"job_id": str(job.id), "kind": job.kind},
            exc_info=True,
        )


async def run_batch(
    sessionmaker: async_sessionmaker[AsyncSession], worker_id: str, limit: int = 10
) -> int:
    """Claim up to `limit` due jobs and run them one after another. Returns how many ran."""
    async with sessionmaker() as session:
        claimed = await claim_due(session, worker_id, limit)
    for job in claimed:
        await run_job(sessionmaker, job)
    return len(claimed)

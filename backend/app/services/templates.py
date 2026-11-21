"""Templates: a planner's saved itinerary skeleton (FR-PLAN-8), and re-planning one for a new day.

A snapshot keeps the title, budget, notes and the stops in order, with no times. A template is
private to the planner who saved it; anyone else's id is a 404.
"""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.caps import SessionMaker
from app.ai.client import AIClient
from app.config import Settings
from app.errors import ApiError
from app.models import Itinerary, PlanTemplate, Stop
from app.providers.registry import Providers
from app.schemas.plan import PlanSessionIn
from app.schemas.template import TemplateOut
from app.services import planning
from app.services.couple import not_found
from app.services.itinerary_access import get_editable

MAX_TEMPLATES = 50  # per planner
MAX_STOPS = 5  # the most a build is asked for


def _cost(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


async def save_template(
    session: AsyncSession,
    couple_id: uuid.UUID,
    viewer_user_id: uuid.UUID,
    itinerary_id: uuid.UUID,
    name: str,
) -> PlanTemplate:
    itinerary = await get_editable(session, viewer_user_id, couple_id, itinerary_id)
    count = (
        await session.execute(
            select(func.count())
            .select_from(PlanTemplate)
            .where(PlanTemplate.planner_user_id == viewer_user_id)
        )
    ).scalar_one()
    if count >= MAX_TEMPLATES:
        raise ApiError(
            409,
            "template_limit",
            f"You can keep up to {MAX_TEMPLATES} templates. Delete one to save another.",
        )
    stops = (
        await session.execute(
            select(Stop).where(Stop.itinerary_id == itinerary.id).order_by(Stop.position)
        )
    ).scalars()
    template = PlanTemplate(
        couple_id=couple_id,
        planner_user_id=viewer_user_id,
        name=name,
        source_itinerary_id=itinerary.id,
        snapshot={
            "title": itinerary.title,
            "budget": _cost(itinerary.budget),
            "backup_plan": itinerary.backup_plan,
            "bring_wear": itinerary.bring_wear,
            "stops": [
                {
                    "title": s.title,
                    "place_id": s.place_id,
                    "description": s.description,
                    "est_cost": _cost(s.est_cost),
                    "notes": s.notes,
                }
                for s in stops
            ],
        },
    )
    session.add(template)
    await session.flush()
    return template


async def list_templates(
    session: AsyncSession, couple_id: uuid.UUID, viewer_user_id: uuid.UUID
) -> list[PlanTemplate]:
    """The viewer's own templates, newest first."""
    rows = await session.execute(
        select(PlanTemplate)
        .where(PlanTemplate.planner_user_id == viewer_user_id, PlanTemplate.couple_id == couple_id)
        .order_by(PlanTemplate.created_at.desc(), PlanTemplate.id)
    )
    return list(rows.scalars())


async def get_own_template(
    session: AsyncSession,
    couple_id: uuid.UUID,
    viewer_user_id: uuid.UUID,
    template_id: uuid.UUID,
) -> PlanTemplate:
    template = (
        await session.execute(
            select(PlanTemplate).where(
                PlanTemplate.id == template_id,
                PlanTemplate.planner_user_id == viewer_user_id,
                PlanTemplate.couple_id == couple_id,
            )
        )
    ).scalar_one_or_none()
    if template is None:
        raise not_found("That template isn't here.")
    return template


async def delete_template(
    session: AsyncSession,
    couple_id: uuid.UUID,
    viewer_user_id: uuid.UUID,
    template_id: uuid.UUID,
) -> None:
    await session.delete(await get_own_template(session, couple_id, viewer_user_id, template_id))
    await session.flush()


def template_out(template: PlanTemplate) -> TemplateOut:
    snapshot = template.snapshot
    return TemplateOut(
        id=template.id,
        name=template.name,
        created_at=template.created_at,
        source_itinerary_id=template.source_itinerary_id,
        title=snapshot.get("title", ""),
        stop_titles=[s["title"] for s in snapshot.get("stops", [])],
    )


async def replan(
    session: AsyncSession,
    *,
    ai: AIClient,
    providers: Providers,
    settings: Settings,
    sessionmaker: SessionMaker,
    couple_id: uuid.UUID,
    requester_user_id: uuid.UUID,
    template_id: uuid.UUID,
    starts_at: dt.datetime,
    ends_at: dt.datetime | None,
) -> Itinerary:
    """A fresh itinerary shaped like the template, for a new day."""
    template = await get_own_template(session, couple_id, requester_user_id, template_id)
    snapshot = template.snapshot
    titles = [s["title"] for s in snapshot.get("stops", [])]
    steer = f"Re-plan this date we loved: {snapshot.get('title', template.name)}."
    if titles:
        steer += f" Stops: {'; '.join(titles)}."
    plan = await planning.create_session(
        session,
        couple_id=couple_id,
        planner_user_id=requester_user_id,
        body=PlanSessionIn(
            window_start=starts_at,
            window_end=ends_at or starts_at + planning.DEFAULT_DATE_LENGTH,
            budget_max=snapshot.get("budget"),
            steer=planning.clip(steer),
            must_include=None,
            surprise=False,
        ),
    )
    if titles:
        plan.inputs = {**plan.inputs, "stop_count": min(len(titles), MAX_STOPS)}
    return await planning.plan_all(
        session,
        ai=ai,
        providers=providers,
        settings=settings,
        sessionmaker=sessionmaker,
        plan=plan,
        requester_user_id=requester_user_id,
        feature="replan",
    )

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(tags=["meta"])
logger = logging.getLogger(__name__)


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "error"]


@router.get(
    "/health",
    response_model=HealthOut,
    responses={503: {"model": HealthOut, "description": "Database unreachable"}},
)
async def health(session: AsyncSession = Depends(get_session)) -> HealthOut | JSONResponse:
    try:
        async with asyncio.timeout(5):
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("health check: database unreachable", exc_info=True)
        return JSONResponse(HealthOut(status="degraded", db="error").model_dump(), status_code=503)
    return HealthOut(status="ok", db="ok")

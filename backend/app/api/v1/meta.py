from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import __version__
from app.config import Settings
from app.deps import get_app_settings

router = APIRouter(tags=["meta"])


class MetaOut(BaseModel):
    demo_mode: bool
    no_login: bool
    version: str


@router.get("/meta", response_model=MetaOut)
async def meta(settings: Settings = Depends(get_app_settings)) -> MetaOut:
    return MetaOut(demo_mode=settings.demo_mode, no_login=settings.no_login, version=__version__)

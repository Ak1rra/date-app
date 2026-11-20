from fastapi import APIRouter

from app.api.v1 import dev, health, meta

router = APIRouter()
router.include_router(health.router)
router.include_router(meta.router)
router.include_router(dev.router)

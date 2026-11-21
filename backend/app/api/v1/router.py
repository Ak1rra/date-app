from fastapi import APIRouter

from app.api.v1 import (
    auth,
    calendar,
    couple,
    dev,
    health,
    ideas,
    itineraries,
    me,
    meta,
    notes,
    notifications,
    places,
    plan,
    profiles,
    surprise,
    templates,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(auth.router)
router.include_router(me.router)
router.include_router(notifications.router)
router.include_router(meta.router)
router.include_router(dev.router)
router.include_router(couple.router)
router.include_router(profiles.router)
router.include_router(itineraries.router)
router.include_router(surprise.router)
router.include_router(calendar.router)
router.include_router(places.router)
router.include_router(notes.router)
router.include_router(ideas.router)
router.include_router(plan.router)
router.include_router(plan.idea_router)
router.include_router(templates.router)

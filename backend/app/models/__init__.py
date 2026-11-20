"""ORM models. Import every model module here so Alembic autogenerate sees them."""

from app.models.base import Base
from app.models.couple import Couple, CoupleMember
from app.models.jobs import Job
from app.models.notes import PlannerNote
from app.models.profile import (
    AvailabilityBlock,
    AvailabilityWindow,
    CoupleConstraints,
    ImportantDate,
    PartnerProfile,
)
from app.models.user import MagicLinkToken, User, UserSession

__all__ = [
    "AvailabilityBlock",
    "AvailabilityWindow",
    "Base",
    "Couple",
    "CoupleConstraints",
    "CoupleMember",
    "ImportantDate",
    "Job",
    "MagicLinkToken",
    "PartnerProfile",
    "PlannerNote",
    "User",
    "UserSession",
]

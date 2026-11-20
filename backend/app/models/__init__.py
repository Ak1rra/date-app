"""ORM models. Import every model module here so Alembic autogenerate sees them."""

from app.models.ai import AIUsage, ProviderCache
from app.models.base import Base
from app.models.chat import ChatMessage, ChatThread
from app.models.couple import Couple, CoupleMember
from app.models.feedback import PlannerRead, Rating, TasteSummary
from app.models.ideas import Idea, Invite, ProfileSuggestion, Vote
from app.models.itinerary import ChecklistItem, Itinerary, Stop, SurpriseHint, TravelLeg
from app.models.jobs import Job
from app.models.notes import PlannerNote
from app.models.notify import Notification, PushSubscription
from app.models.plan import PlanSession, PlanTemplate
from app.models.profile import (
    AvailabilityBlock,
    AvailabilityWindow,
    CoupleConstraints,
    ImportantDate,
    PartnerProfile,
)
from app.models.user import MagicLinkToken, User, UserSession

__all__ = [
    "AIUsage",
    "AvailabilityBlock",
    "AvailabilityWindow",
    "Base",
    "ChatMessage",
    "ChatThread",
    "ChecklistItem",
    "Couple",
    "CoupleConstraints",
    "CoupleMember",
    "Idea",
    "ImportantDate",
    "Invite",
    "Itinerary",
    "Job",
    "MagicLinkToken",
    "Notification",
    "PartnerProfile",
    "PlanSession",
    "PlanTemplate",
    "PlannerNote",
    "PlannerRead",
    "ProfileSuggestion",
    "ProviderCache",
    "PushSubscription",
    "Rating",
    "Stop",
    "SurpriseHint",
    "TasteSummary",
    "TravelLeg",
    "User",
    "UserSession",
    "Vote",
]

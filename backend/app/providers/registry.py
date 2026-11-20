"""Picks provider implementations from Settings. Both functions are FastAPI dependencies,
so tests swap them with `app.dependency_overrides`."""

from fastapi import Depends

from app.config import Settings
from app.deps import get_app_settings
from app.errors import ApiError
from app.providers.email.base import EmailSender
from app.providers.email.console import ConsoleEmailSender
from app.providers.email.resend import ResendEmailSender
from app.providers.geocoding.base import Geocoder
from app.providers.geocoding.fake import FakeGeocoder


def get_email_sender(settings: Settings = Depends(get_app_settings)) -> EmailSender:
    if settings.effective_email_provider == "console":
        return ConsoleEmailSender()
    if not settings.email_api_key or not settings.email_from:
        raise ApiError(503, "provider_unavailable", "Email isn't available right now.")
    return ResendEmailSender(settings.email_api_key, settings.email_from)


def get_geocoder(settings: Settings = Depends(get_app_settings)) -> Geocoder:
    if settings.use_fake_geocoder:
        return FakeGeocoder()
    raise ApiError(503, "provider_unavailable", "Location lookup isn't available right now.")

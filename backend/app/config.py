"""Application settings, read from the environment / .env (names as in .env.example)."""

import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # Core
    demo_mode: bool = False
    app_origin: str = "http://localhost:8090"
    database_url: str = "postgresql+asyncpg://dateapp:dateapp@localhost:5432/dateapp"
    test_database_url: str = "postgresql+asyncpg://dateapp:dateapp@localhost:5432/dateapp_test"
    cookie_secure: bool = True
    session_ttl_days: int = 30
    magic_link_ttl_minutes: int = 15
    log_level: str = "INFO"
    sentry_dsn: str | None = None
    # Expose /api/v1/dev/outbox (sign-in links, unauthenticated). Local dev only; DEMO_MODE
    # alone is deliberately not enough.
    dev_outbox: bool = False
    # Replaces sign-in with a husband/wife picker (POST /auth/choose-profile); email sign-in
    # is switched off. No PIN, no protection: anyone who can reach the app can act as either
    # profile.
    no_login: bool = False
    port: int = 8090
    # Proxies whose X-Forwarded-* headers `dateapp serve` trusts (uvicorn forwarded_allow_ips).
    forwarded_allow_ips: str = "127.0.0.1"
    # Built frontend served by the API process. None = <repo root>/frontend/dist.
    frontend_dist: Path | None = None
    # Run the job worker inside the API process (lifespan task).
    run_worker: bool = True

    # Providers (unused in M1 unless noted, but declared)
    openai_api_key: str | None = None
    ai_model_fast: str = "gpt-6-luna"
    ai_model_smart: str = "gpt-6.2-sol"
    # AI caps, effort & pricing (M2)
    ai_cap_ideas_per_day: int = 20
    ai_cap_builds_per_day: int = 20
    ai_cap_chat_per_day: int = 100
    ai_monthly_tokens_per_couple: int = 4_000_000
    ai_global_monthly_usd: float = 25.0
    ai_max_tool_iterations: int = 8
    ai_effort_smart: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    ai_effort_fast: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    # $ per million tokens, per model. A model that isn't listed is priced as the smart model.
    ai_prices: dict[str, dict[str, float]] = {
        "gpt-6-luna": {
            "input": 0.10,
            "output": 0.50,
            "cache_read": 0.01,
            "cache_write": 0.10,  # priced at the input rate so caps never undercount
        },
        "gpt-6.2-sol": {
            "input": 2.00,
            "output": 10.00,
            "cache_read": 0.10,
            "cache_write": 2.00,
        },
    }
    travel_buffer_minutes: int = 10
    google_maps_api_key: str | None = None
    ticketmaster_api_key: str | None = None
    weather_provider: str = "open-meteo"
    email_provider: Literal["console", "resend"] = "console"
    email_api_key: str | None = None
    email_from: str | None = None
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str | None = None

    # Provider caching & rate limits (M2)
    # TTLs in seconds. Only Ticketmaster and Open-Meteo responses are cached in the database:
    # Google Maps Platform terms forbid storing Places, Routes and Geocoding content.
    cache_ttl_events: int = 21600
    cache_ttl_weather: int = 3600
    # Venue snapshots on itinerary stops older than this are refreshed by place id on read.
    venue_snapshot_max_age_days: int = 30
    # At least 1: a limit of 0 or less would reject every call.
    provider_calls_per_couple_per_minute: int = Field(default=60, ge=1)
    provider_calls_global_per_minute: int = Field(default=300, ge=1)

    @model_validator(mode="after")
    def _check_ai_prices(self) -> "Settings":
        """Fail at startup on a price map that would make the spend caps undercount: a missing
        or negative rate counts as free, and a model with no entry has no rates to fall back on."""
        keys = {"input", "output", "cache_read", "cache_write"}
        for model in (self.ai_model_smart, self.ai_model_fast):
            if model not in self.ai_prices:
                raise ValueError(f"AI_PRICES has no entry for the configured model {model!r}")
        for model, price in self.ai_prices.items():
            if set(price) != keys:
                raise ValueError(f"AI_PRICES[{model!r}] must have exactly the keys {sorted(keys)}")
            for key, value in price.items():
                if not math.isfinite(value) or value < 0:
                    raise ValueError(
                        f"AI_PRICES[{model!r}][{key!r}] must be a finite number >= 0, got {value}"
                    )
        return self

    @property
    def resolved_frontend_dist(self) -> Path:
        return self.frontend_dist or REPO_ROOT / "frontend" / "dist"

    @property
    def origin(self) -> str:
        """`app_origin` without a trailing slash, as browsers send it in `Origin`."""
        return self.app_origin.rstrip("/")

    @property
    def effective_email_provider(self) -> Literal["console", "resend"]:
        """DEMO_MODE forces the console sender whatever else is set."""
        return "console" if self.demo_mode else self.email_provider

    @property
    def use_fake_geocoder(self) -> bool:
        """The fake geocoder is used in DEMO_MODE unless GOOGLE_MAPS_API_KEY is set (a real key
        always wins, so real keys work with or without DEMO_MODE)."""
        return self.demo_mode and not self.google_maps_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()

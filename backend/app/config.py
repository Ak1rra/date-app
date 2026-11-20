"""Application settings, read from the environment / .env (names as in .env.example)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    anthropic_api_key: str | None = None
    ai_model_fast: str = "claude-haiku-5-5"
    ai_model_smart: str = "claude-sonnet-5-5"
    google_maps_api_key: str | None = None
    ticketmaster_api_key: str | None = None
    weather_provider: str = "open-meteo"
    email_provider: Literal["console", "resend"] = "console"
    email_api_key: str | None = None
    email_from: str | None = None
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str | None = None
    apple_client_id: str | None = None
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_private_key: str | None = None
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None

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
        """DEMO_MODE forces the fake geocoder; no real geocoder exists yet."""
        return self.demo_mode


@lru_cache
def get_settings() -> Settings:
    return Settings()

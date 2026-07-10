"""config.py — environment-driven settings (Pydantic Settings)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings sourced from env (and an optional .env file)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Identity (MF1 /version contract).
    service: str = "windy-registry"
    environment: str = "development"  # development | staging | production
    commit_sha: str | None = Field(default=None, validation_alias="COMMIT_SHA")
    build_timestamp: str | None = Field(default=None, validation_alias="BUILD_TIMESTAMP")

    # API
    host: str = "0.0.0.0"
    port: int = 8500
    log_level: str = "info"

    # Database (used once WD-14 lands; nullable so /health can start without it).
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    redis_url: str | None = Field(default=None, validation_alias="REDIS_URL")

    # R2 (used once WD-13 lands).
    r2_account_id: str | None = Field(default=None, validation_alias="R2_ACCOUNT_ID")
    r2_bucket: str = "windydrops-bundles"
    r2_public_domain: str = "drops.windydrops.com"
    r2_access_key_id: str | None = Field(default=None, validation_alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str | None = Field(default=None, validation_alias="R2_SECRET_ACCESS_KEY")

    # JWKS URLs (used by WD-15 dual-JWKS auth middleware).
    pro_jwks_url: str = "https://account.windyword.ai/.well-known/jwks.json"
    eternitas_jwks_url: str = "https://api.eternitas.ai/.well-known/eternitas-keys"

    # Passport revocation enforcement (finding A4 — mirror windy-search #57).
    # The EPT is a 365-day offline bearer; the CRL catches revocations issued
    # after mint. Init'd only outside dev (main.create_app) so tests make no
    # network call; unreachable past crl_max_stale_seconds → fail CLOSED.
    eternitas_crl_url: str = "https://api.eternitas.ai/.well-known/eternitas-crl"
    crl_ttl_seconds: int = 30
    crl_max_stale_seconds: int = 300
    revocation_fail_closed: bool | None = None  # None → closed iff production

    # Rate limiting.
    rate_limit_unauthenticated: str = "100/minute"
    rate_limit_user: str = "1000/minute"


@lru_cache
def get_settings() -> Settings:
    return Settings()

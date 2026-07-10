"""main.py — FastAPI app factory for windy-registry.

The app is intentionally minimal at WD-12 — only /version (MF1) and /health
endpoints. DB lifespan, R2 wiring, auth middleware, and the real domain
routes land in WD-13 through WD-21.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .config import get_settings
from .routes import (
    authors,
    browse,
    drops,
    federation,
    health,
    library,
    ratings,
    stripe_connect,
    tips,
    version,
    webhooks,
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Windy Drops Registry",
        description="Registry service for the Windy Drops marketplace.",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings

    # Passport revocation cache (A4). Init only outside dev so the test suite
    # makes no CRL network call; the rev-claim + issuer checks in
    # middleware/auth.py run regardless of whether the cache is wired.
    if settings.environment != "development":
        from .middleware.revocation import init_revocation_cache
        fail_closed = (
            settings.revocation_fail_closed
            if settings.revocation_fail_closed is not None
            else settings.environment == "production"
        )
        init_revocation_cache(
            settings.eternitas_crl_url,
            ttl_seconds=settings.crl_ttl_seconds,
            max_stale_seconds=settings.crl_max_stale_seconds,
            fail_closed=fail_closed,
        )

    # G11: rate limit middleware. Disabled in dev + tests so the suite stays
    # deterministic; enabled in production.
    from .middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(
        RateLimitMiddleware,
        unauth_rate=settings.rate_limit_unauthenticated,
        auth_rate=settings.rate_limit_user,
        enabled=(settings.environment == "production"),
    )

    # CORS: the marketplace frontend (windydrops.com) fetches this API directly
    # from the browser. Without these headers the browser blocks every request
    # and the Browse page shows "Couldn't load drops — Failed to fetch". Allow
    # the marketplace's own origins (added last → outermost, so even rate-limited
    # 429 responses carry the CORS headers).
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://windydrops.com",
            "https://www.windydrops.com",
            "https://windydrops.pages.dev",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    app.include_router(version.router)
    app.include_router(health.router)
    app.include_router(drops.router)
    app.include_router(library.router)
    app.include_router(browse.router)
    app.include_router(browse.public_router)
    app.include_router(ratings.router)
    app.include_router(webhooks.router)
    app.include_router(authors.authors_router)
    app.include_router(authors.follows_router)
    app.include_router(federation.router)
    app.include_router(stripe_connect.router)
    app.include_router(tips.router)
    return app


app = create_app()

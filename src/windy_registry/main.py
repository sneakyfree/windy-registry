"""main.py — FastAPI app factory for windy-registry.

The app is intentionally minimal at WD-12 — only /version (MF1) and /health
endpoints. DB lifespan, R2 wiring, auth middleware, and the real domain
routes land in WD-13 through WD-21.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .config import get_settings
from .routes import drops, health, library, version


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
    app.include_router(version.router)
    app.include_router(health.router)
    app.include_router(drops.router)
    app.include_router(library.router)
    return app


app = create_app()

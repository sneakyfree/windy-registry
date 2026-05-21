"""database.py — async SQLAlchemy 2.0 engine + session factory.

The engine is lazily created so the app can boot for /health and /version
without a database (matches MF1 invariant: "must answer during incidents").
WD-14 strand.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    """Single base class every ORM model inherits from."""


_engine: Any | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> Any:
    global _engine
    if _engine is None:
        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError(
                "DATABASE_URL is not configured. Set it in .env or env var to enable DB access."
            )
        _engine = create_async_engine(
            settings.database_url,
            echo=(settings.environment == "development"),
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session, commits on success, rolls back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

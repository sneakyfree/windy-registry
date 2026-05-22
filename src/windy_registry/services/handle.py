"""handle.py — deterministic author handle derivation.

Per ADR-053 §"Author profiles & social graph": "handle derived deterministically
from passport (or callsign)". Algorithm (F13 + G9):

  1. If author entry has a callsign → handle = lowercased + sanitized callsign.
  2. Otherwise if passport present → handle = "u-<last-8-passport-chars-lowercased>".
  3. Otherwise → handle = derived from name slug.
  4. Collision: suffix with "-2", "-3", ... until unique.

The handle uniqueness check is the caller's job (uses the live DB).
"""

from __future__ import annotations

import re
from typing import Any

_SAFE = re.compile(r"[^a-z0-9-]+")


def _slug(s: str) -> str:
    s = s.strip().lower().replace(" ", "-").replace("_", "-")
    s = _SAFE.sub("-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:48] if s else "anon"


def derive_handle_candidates(author: dict[str, Any]) -> list[str]:
    """Return ordered candidate handles for an author entry. First is preferred."""
    candidates: list[str] = []
    callsign = (author.get("callsign") or "").strip()
    passport = (author.get("passport") or "").strip()
    name = (author.get("name") or "").strip()

    if callsign:
        candidates.append(_slug(callsign))
    if passport:
        # ET26-OCKM-Y005 → "u-ockmy005" (last 8 alphanumeric)
        clean = re.sub(r"[^A-Za-z0-9]", "", passport)
        if len(clean) >= 8:
            candidates.append(f"u-{clean[-8:].lower()}")
    if name and not callsign:
        candidates.append(_slug(name))

    if not candidates:
        candidates.append("anon")
    return candidates


async def ensure_unique_handle(
    session,
    base: str,
    *,
    passport: str | None = None,
) -> str:
    """Suffix-disambiguate against existing Authors. Returns the first free handle.

    If `passport` is provided and the existing Authors row with the same handle
    is owned by THIS passport, returns the unsuffixed handle (idempotent).
    """
    from sqlalchemy import select

    from ..models import Author

    candidate = base
    suffix = 1
    while True:
        existing = (await session.execute(
            select(Author).where(Author.handle == candidate)
        )).scalar_one_or_none()
        if existing is None:
            return candidate
        if passport and existing.passport == passport:
            return candidate
        suffix += 1
        candidate = f"{base}-{suffix}"

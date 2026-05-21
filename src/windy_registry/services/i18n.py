"""i18n.py — Accept-Language → best-locale resolver for manifest i18n fields.

Per ADR-053 §"Internationalization": `name` and `subtitle` accept i18n object
form `{en: "...", ko: "...", default: "en"}`. This service picks the best
match for a request's Accept-Language header and falls back to the object's
`default` key.

Closes gap F14 — surfaces no longer return the raw object; clients see a
resolved string per their preferred locale.
"""

from __future__ import annotations

from typing import Any


def parse_accept_language(header: str | None) -> list[str]:
    """Return the locale tags from an Accept-Language header in priority order.
    'en-US,en;q=0.9,ko;q=0.6' -> ['en-us', 'en', 'ko']
    """
    if not header:
        return []
    out: list[tuple[float, str]] = []
    for part in header.split(","):
        token = part.strip()
        if not token:
            continue
        if ";" in token:
            tag, *params = token.split(";")
            q = 1.0
            for p in params:
                p = p.strip()
                if p.startswith("q="):
                    try:
                        q = float(p[2:])
                    except ValueError:
                        pass
            out.append((q, tag.strip().lower()))
        else:
            out.append((1.0, token.lower()))
    out.sort(key=lambda x: -x[0])
    return [tag for _, tag in out]


def resolve_i18n(value: Any, accept_language: str | None = None) -> Any:
    """Resolve an i18n object to a single string using the request's preferred
    locales. Plain strings pass through unchanged. Returns None if value is None.

    Match priority:
      1. Exact tag match (e.g., 'en-US' matches 'en-us')
      2. Language-only match (e.g., 'en-US' matches 'en')
      3. The object's `default` key (whose value is itself a locale tag)
      4. First available value
    """
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return value

    preferences = parse_accept_language(accept_language)
    default_tag = value.get("default")

    candidates = preferences + ([default_tag] if default_tag else [])
    # Try exact + prefix matches.
    for pref in candidates:
        if not pref:
            continue
        if pref in value:
            return value[pref]
        # Prefix match: 'en-us' tries 'en'
        prefix = pref.split("-")[0]
        if prefix != pref and prefix in value:
            return value[prefix]

    # Fall back to first non-default value.
    for k, v in value.items():
        if k != "default" and isinstance(v, str):
            return v
    return None


def resolve_manifest_i18n_fields(
    manifest: dict[str, Any],
    accept_language: str | None,
) -> dict[str, Any]:
    """Return a copy of `manifest` with i18n fields resolved to strings.
    Currently resolves: name, subtitle.
    """
    if not isinstance(manifest, dict):
        return manifest
    out = dict(manifest)
    for field in ("name", "subtitle"):
        if field in out:
            out[field] = resolve_i18n(out[field], accept_language)
    return out

"""canonical.py — same recursive lex-sort, compact-JSON serialization as the
SDKs (packages/sdk/src/lib/canonical.ts + python/sdk/.../canonical.py).

The registry MUST agree byte-for-byte with what the SDK signed; otherwise
signature verification will fail.
"""

from __future__ import annotations

import json
from typing import Any


def canonicalize(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

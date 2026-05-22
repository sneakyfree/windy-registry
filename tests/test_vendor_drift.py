"""test_vendor_drift.py — drift guard for the vendored windy_drops_spec package.

The canonical source lives in
  sneakyfree/windy-drops:python/artifact-spec/src/windy_drops_spec/
The vendored copy lives in
  src/windy_drops_spec/

When both repos are checked out side-by-side under $HOME, this test asserts
byte-identity. When the canonical repo isn't present (CI in a registry-only
environment, foreign machines), the test no-ops with a clear log message — CI
must not fail just because a sibling repo happens not to be on disk.

Mirrors the pattern in sneakyfree/windy-pro:account-server/tests/control-panel-schema-drift.test.ts.
See src/windy_drops_spec/VENDOR.md for the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

VENDORED_DIR = Path(__file__).resolve().parent.parent / "src" / "windy_drops_spec"
CANONICAL_DIR = (
    Path.home() / "windy-drops" / "python" / "artifact-spec" / "src" / "windy_drops_spec"
)

FILES = ("__init__.py", "_generated.py")

_canonical_available = all((CANONICAL_DIR / f).is_file() for f in FILES)


@pytest.mark.skipif(
    not _canonical_available,
    reason=(
        f"canonical windy_drops_spec not present at {CANONICAL_DIR} — "
        "skipping drift check (registry-only environment)"
    ),
)
@pytest.mark.parametrize("filename", FILES)
def test_vendored_file_matches_canonical(filename: str) -> None:
    vendored = (VENDORED_DIR / filename).read_bytes()
    canonical = (CANONICAL_DIR / filename).read_bytes()
    assert vendored == canonical, (
        f"src/windy_drops_spec/{filename} has drifted from canonical at "
        f"{CANONICAL_DIR / filename}. Re-vendor per the runbook in "
        "src/windy_drops_spec/VENDOR.md."
    )

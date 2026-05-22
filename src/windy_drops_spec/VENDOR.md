# Vendored: `windy_drops_spec`

This directory is a vendored copy of the canonical Python bindings package from
`sneakyfree/windy-drops:python/artifact-spec/src/windy_drops_spec/`.

## Why vendored

The canonical package is not yet published to PyPI. Vendoring keeps the registry
self-deployable today without a PyPI publishing gate, and the vendor + drift-guard
pattern is the documented preference for cross-repo schema sharing in this
ecosystem (see the team's memory: `feedback_vendor_drift_guard_pattern`).

Trade-off accepted: vendored code can rot. The drift guard (`tests/test_vendor_drift.py`)
catches divergence whenever both repos are checked out side-by-side under `$HOME`.
CI in a registry-only environment skips the check cleanly.

## When to update

Update this vendor whenever:

1. `~/windy-drops/schemas/windy.drop.v1.json` changes and the canonical Python
   bindings are regenerated (via `datamodel-code-generator`).
2. `~/windy-drops/python/artifact-spec/src/windy_drops_spec/__init__.py` adds a
   new hand-curated constant.

The drift test will fail with a clear message pointing here if you forget.

## How to update

```bash
cp ~/windy-drops/python/artifact-spec/src/windy_drops_spec/__init__.py \
   ~/windy-registry/src/windy_drops_spec/__init__.py
cp ~/windy-drops/python/artifact-spec/src/windy_drops_spec/_generated.py \
   ~/windy-registry/src/windy_drops_spec/_generated.py
cd ~/windy-registry && .venv/bin/python -m pytest tests/test_vendor_drift.py
```

Once the canonical package is published to PyPI as `windy-drops-spec`, this
vendor directory can be deleted in favor of a normal dependency declaration.

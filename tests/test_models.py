"""test_models.py — WD-14 smoke tests for the ORM models.

These tests don't require a real Postgres instance — they just verify that
the SQLAlchemy metadata is consistent and every model resolves cleanly.

The full upgrade/downgrade roundtrip (MF7) lives in test_migrations.py and
requires a running Postgres + pgvector extension (CI-only).
"""

from __future__ import annotations

import pytest

from windy_registry.database import Base
from windy_registry.models import (
    Author,
    Drop,
    DropVersion,
    Follow,
    Fork,
    Purchase,
    Rating,
    Refund,
    Tip,
    UserLibrary,
    WebhookDelivery,
    WebhookSubscription,
)


def test_all_models_registered_with_base_metadata() -> None:
    """Every model class shows up in Base.metadata exactly once."""
    expected = {
        "authors",
        "drop_versions",
        "drops",
        "follows",
        "forks",
        "purchases",
        "ratings",
        "refunds",
        "tips",
        "user_library",
        "webhook_deliveries",
        "webhook_subscriptions",
    }
    assert set(Base.metadata.tables.keys()) == expected


def test_models_resolve_cleanly() -> None:
    """Every imported model has a __tablename__ matching expectations."""
    pairs = [
        (Author, "authors"),
        (Drop, "drops"),
        (DropVersion, "drop_versions"),
        (Follow, "follows"),
        (Fork, "forks"),
        (Purchase, "purchases"),
        (Rating, "ratings"),
        (Refund, "refunds"),
        (Tip, "tips"),
        (UserLibrary, "user_library"),
        (WebhookDelivery, "webhook_deliveries"),
        (WebhookSubscription, "webhook_subscriptions"),
    ]
    for model, name in pairs:
        assert model.__tablename__ == name, f"{model.__name__} -> {model.__tablename__}"


def test_drop_has_versions_relationship() -> None:
    """sanity check on the relationship() wiring."""
    assert hasattr(Drop, "versions")


@pytest.mark.parametrize(
    "model,col_name",
    [
        (DropVersion, "signature_verified"),
        (UserLibrary, "auto_update"),
        (Author, "stripe_charges_enabled"),
    ],
)
def test_boolean_columns_have_dialect_safe_defaults(model: type, col_name: str) -> None:
    """Per feedback_boolean_server_default_dialect_trap: must use sa.false()/sa.true(),
    NOT sa.text('0') (works in SQLite, crashes Postgres fresh deploy).
    """
    col = model.__table__.c[col_name]
    server_default = col.server_default
    assert server_default is not None
    # Render is a SQLA-internal — convert to string and assert no bare "0"/"1".
    rendered = str(server_default.arg)
    assert rendered in ("false", "true", "FALSE", "TRUE"), (
        f"{model.__name__}.{col_name} server_default is {rendered!r}; "
        "must be sa.false()/sa.true() per feedback_boolean_server_default_dialect_trap"
    )


def test_check_constraint_on_rating_stars() -> None:
    """The 1-5 star CHECK constraint is named so downgrade can drop it."""
    constraints = Rating.__table__.constraints
    names = {c.name for c in constraints if c.name}
    assert "rating_stars_range" in names

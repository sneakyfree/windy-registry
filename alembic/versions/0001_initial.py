"""initial schema — drops, drop_versions, forks, authors, follows, library, ratings, webhooks, tips, purchases, refunds

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-21

WD-14 — every migration ships a tested downgrade() per MF7 (schema
migration reversibility in CI). The downgrade reverses upgrade() exactly;
verified by test_migrations.py::test_upgrade_downgrade_roundtrip.

Booleans use sa.false() / sa.true() per feedback_boolean_server_default_dialect_trap
(sa.text("0") is SQLite-tolerated but Postgres-rejected).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector for M9+ trending (embedding similarity).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "drops",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("current_version", sa.String(64), nullable=False),
        sa.Column(
            "forked_from",
            sa.String(128),
            sa.ForeignKey("drops.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_drops_type", "drops", ["type"])
    op.create_index("ix_drops_forked_from", "drops", ["forked_from"])

    op.create_table(
        "drop_versions",
        sa.Column("drop_id", sa.String(128), sa.ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.String(64), primary_key=True),
        sa.Column("manifest", JSONB, nullable=False),
        sa.Column("bundle_url", sa.Text, nullable=False),
        sa.Column("bundle_sha256", sa.String(64), nullable=False),
        sa.Column("signature_verified", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("signer_passport", sa.String(64), nullable=True),
        sa.Column("signer_integrity_band", sa.String(32), nullable=True),
        sa.Column("signer_clearance_level", sa.String(32), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_drop_versions_signer_passport", "drop_versions", ["signer_passport"])

    op.create_table(
        "forks",
        sa.Column("source_drop_id", sa.String(128), sa.ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("fork_drop_id", sa.String(128), sa.ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("is_published", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("forked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "authors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("handle", sa.String(64), unique=True, nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("passport", sa.String(64), unique=True, nullable=True),
        sa.Column("integrity_band", sa.String(32), nullable=True),
        sa.Column("clearance_level", sa.String(32), nullable=True),
        sa.Column("integrity_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_account_id", sa.String(64), nullable=True),
        sa.Column("stripe_charges_enabled", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("stripe_payouts_enabled", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("stripe_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("public_tips_disabled", sa.Boolean, server_default=sa.false(), nullable=False),
        sa.Column("follower_count_cached", sa.Integer, server_default="0", nullable=False),
        sa.Column("lifetime_tips_cents", sa.Integer, server_default="0", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_authors_handle", "authors", ["handle"])
    op.create_index("ix_authors_passport", "authors", ["passport"])

    op.create_table(
        "follows",
        sa.Column("follower_user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "followed_handle",
            sa.String(64),
            sa.ForeignKey("authors.handle", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "user_library",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("drop_id", sa.String(128), sa.ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("auto_update", sa.Boolean, server_default=sa.true(), nullable=False),
    )

    op.create_table(
        "ratings",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("drop_id", sa.String(128), sa.ForeignKey("drops.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("stars", sa.Integer, nullable=False),
        sa.Column("review", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("stars BETWEEN 1 AND 5", name="rating_stars_range"),
    )

    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("callback_url", sa.Text, nullable=False),
        sa.Column("event_types", JSONB, nullable=False),
        sa.Column("secret_hash", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer, server_default="0", nullable=False),
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "subscription_id",
            UUID(as_uuid=True),
            sa.ForeignKey("webhook_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("response_body_trunc", sa.String(1024), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer, server_default="0", nullable=False),
    )
    op.create_index("ix_webhook_deliveries_subscription_id", "webhook_deliveries", ["subscription_id"])
    op.create_index("ix_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"])

    op.create_table(
        "tips",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("drop_id", sa.String(128), sa.ForeignKey("drops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("author_handle", sa.String(64), nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("stripe_session_id", sa.String(128), unique=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tips_drop_id", "tips", ["drop_id"])
    op.create_index("ix_tips_user_id", "tips", ["user_id"])
    op.create_index("ix_tips_author_handle", "tips", ["author_handle"])

    op.create_table(
        "purchases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("drop_id", sa.String(128), sa.ForeignKey("drops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("buyer_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("seller_handle", sa.String(64), nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(128), unique=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_purchases_drop_id", "purchases", ["drop_id"])
    op.create_index("ix_purchases_buyer_user_id", "purchases", ["buyer_user_id"])
    op.create_index("ix_purchases_seller_handle", "purchases", ["seller_handle"])

    op.create_table(
        "refunds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "purchase_id",
            UUID(as_uuid=True),
            sa.ForeignKey("purchases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("stripe_refund_id", sa.String(128), unique=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("refunds")
    op.drop_table("purchases")
    op.drop_table("tips")
    op.drop_index("ix_webhook_deliveries_event_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_subscription_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_subscriptions")
    op.drop_table("ratings")
    op.drop_table("user_library")
    op.drop_table("follows")
    op.drop_index("ix_authors_passport", table_name="authors")
    op.drop_index("ix_authors_handle", table_name="authors")
    op.drop_table("authors")
    op.drop_table("forks")
    op.drop_index("ix_drop_versions_signer_passport", table_name="drop_versions")
    op.drop_table("drop_versions")
    op.drop_index("ix_drops_forked_from", table_name="drops")
    op.drop_index("ix_drops_type", table_name="drops")
    op.drop_table("drops")
    # Leave the vector extension in place — other tables may use it.

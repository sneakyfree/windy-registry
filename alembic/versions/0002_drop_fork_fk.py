"""Drop the FK on forks.fork_drop_id — forks claim their id BEFORE publish.

POST /drops/{id}/fork registers lineage ahead of the fork's first publish
(is_published=False until then), so fork_drop_id references a drops row that
does not exist yet. The FK made every fork 500 on Postgres
(ForeignKeyViolationError); SQLite tests passed because SQLite does not
enforce FKs by default.

Revision ID: 0002_drop_fork_fk
Revises: 0001_initial
"""

from __future__ import annotations

from alembic import op

revision = "0002_drop_fork_fk"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("forks_fork_drop_id_fkey", "forks", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key(
        "forks_fork_drop_id_fkey",
        "forks",
        "drops",
        ["fork_drop_id"],
        ["id"],
        ondelete="CASCADE",
    )

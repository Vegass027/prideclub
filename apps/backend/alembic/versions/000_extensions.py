"""extensions

Revision ID: 000_extensions
Revises:
Create Date: 2026-01-01 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "000_extensions"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements;")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_stat_statements;")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto;")
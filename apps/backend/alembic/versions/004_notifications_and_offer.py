"""notifications and offer columns — no-op (already in 001_initial_schema)

Revision ID: 004_notifications_and_offer
Revises: 003_migrate_bonus_points
Create Date: 2026-01-01 00:00:04.000000
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = "004_notifications_and_offer"
down_revision: str | None = "003_migrate_bonus_points"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""habits: archived_at + partial index for active clubs

Revision ID: 007_habit_admin_fields
Revises: 006_suspicious_pairs_index
Create Date: 2026-07-21 21:20:00.000000

Админский флоу управления клубами (см. TZ_kharakteristiki_personazha.md §3.6).

- archived_at TIMESTAMPTZ NULL — soft-delete timestamp. NULL = клуб не в архиве.
- ix_habits_active — частичный индекс, обслуживает горячий путь GET /marketplace:
  WHERE is_active = true AND archived_at IS NULL. Размер индекса держится
  минимальным даже если архивных клубов накопится много.

Никаких CHECK constraints не добавляем: archived_at IS NULL при создании
клуба гарантируется на уровне приложения (DEFAULT не ставим — поле nullable).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007_habit_admin_fields"
down_revision: str | None = "006_suspicious_pairs_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "habits",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_habits_active",
        "habits",
        ["is_active"],
        postgresql_where=sa.text("is_active = true AND archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_habits_active", table_name="habits")
    op.drop_column("habits", "archived_at")

"""users: GDPR columns (deleted_at, data_anonymized)

Revision ID: 005_users_gdpr_columns
Revises: 004_notifications_and_offer
Create Date: 2026-07-21 10:25:00.000000

ФЗ-152: право на удаление ПДн и обезличивание.
- deleted_at — soft-delete timestamp, NULL = пользователь активен
- data_anonymized — TRUE после выполнения права на удаление (username/first_name заменены на NULL/anonymous)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005_users_gdpr_columns"
down_revision: str | None = "004_notifications_and_offer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "data_anonymized",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_users_deleted_at",
        "users",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_column("users", "data_anonymized")
    op.drop_column("users", "deleted_at")

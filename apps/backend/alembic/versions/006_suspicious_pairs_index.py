"""suspicious_pairs: index for listing flagged pairs by status

Revision ID: 006_suspicious_pairs_index
Revises: 005_users_gdpr_columns
Create Date: 2026-07-21 12:00:00.000000

Админ-листинг фильтрует по status='flagged' и сортирует по detected_at DESC.
Частичный индекс сильно меньше полного, потому что 'banned'/'cleared' редки
по сравнению с 'flagged'.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "006_suspicious_pairs_index"
down_revision: Union[str, None] = "005_users_gdpr_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_suspicious_pairs_flagged_recent",
        "suspicious_pairs",
        ["detected_at"],
        postgresql_where=sa.text("status = 'flagged'"),
    )


def downgrade() -> None:
    op.drop_index("ix_suspicious_pairs_flagged_recent", table_name="suspicious_pairs")

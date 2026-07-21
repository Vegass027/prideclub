"""migrate bonus points from memberships to users

Revision ID: 003_migrate_bonus_points
Revises: 002_bonus_and_penalty_fixes
Create Date: 2026-01-01 00:00:03.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "003_migrate_bonus_points"
down_revision: Union[str, None] = "002_bonus_and_penalty_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM memberships WHERE bonus_points < 0) THEN
                RAISE EXCEPTION 'negative bonus_points found — manual cleanup required';
            END IF;
            IF EXISTS (SELECT 1 FROM users WHERE bonus_points > 0) THEN
                RAISE EXCEPTION 'users.bonus_points already populated — manual review required';
            END IF;
        END $$;
        """
    )

    op.execute(
        """
        UPDATE users u SET bonus_points = COALESCE((
            SELECT SUM(m.bonus_points) FROM memberships m WHERE m.user_id = u.id
        ), 0)
        WHERE EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id AND m.bonus_points > 0);
        """
    )

    op.execute("UPDATE memberships SET bonus_points = 0 WHERE bonus_points > 0;")


def downgrade() -> None:
    # Не восстанавливаем memberships.bonus_points — пользователь должен решить вручную.
    pass
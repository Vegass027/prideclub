"""memberships: DROP COLUMN deposit_balance (Pravki-deposit-sse.md §Z-2.1).

Шаг 2 из двух. Запускается минимум через 1 день после 014a, чтобы было окно
для проверки что бэкенд/воркер/бот не упали на новой схеме users.deposit_balance.

Зачем:
    Депозит переехал на users.deposit_balance (014a). Поле memberships.deposit_balance
    больше никто не читает и не пишет. Держим его как legacy-копилку можно
    было только до момента, когда все join'ы/топ-апы работают через user-level.
    После PR #1 — можно безопасно DROP'нуть.

Downgrade:
    Восстанавливает memberships.deposit_balance = users.deposit_balance (по
    активным memberships). На даунгрейде безопасно потерять неактивные (LEFT/PAUSED)
    membership'ы — они уже out of game.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "014b_drop_membership_dep"
down_revision: str | None = "014a_user_deposit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("memberships", "deposit_balance")


def downgrade() -> None:
    # Восстанавливаем колонку с дефолтом 0.
    op.add_column(
        "memberships",
        sa.Column(
            "deposit_balance",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment="LEGACY (014b): глобальный депозит пользователя после 014b живёт на users.deposit_balance",
        ),
    )
    # Backfill из users.deposit_balance в активные membership'ы.
    # Распределяем поровну (round-robin не имеет смысла — все membership'ы одного
    # юзера получат одинаковую сумму). Реально — для даунгрейда с уже потерянной
    # историей распределения это best-effort.
    op.execute(
        """
        UPDATE memberships m
        SET deposit_balance = COALESCE(
            (SELECT deposit_balance FROM users WHERE id = m.user_id),
            0
        )
        WHERE m.status = 'active'
        """
    )

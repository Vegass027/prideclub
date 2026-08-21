"""habits.catcher_amount_kopecks: ADD COLUMN (catcher deposit share).

EXECUTION-PLAN-2026-08-19.md Phase 1 Task 1.1 (rebuild 2026-08-21):
новая механика штрафов — штраф делится на 2 части: часть в призовой фонд клуба +
часть на депозит ловца. Админ клуба при настройке указывает фиксированную сумму
ловцу в копейках (как penalty_amount — никаких basis points / процентов).

DEFAULT 0 = для существующих клубов работает по-старому (всё в фонд,
обратная совместимость). Изменения в логике `apply_catch` — отдельной задачей
Phase 1 Task 1.3, в этой миграции только ADDITIVE schema change.

⚠️ transactions.type — VARCHAR(64), не Postgres ENUM:
- Подтверждение: apps/backend/alembic/versions/001_initial_schema.py:105
  (`sa.Column("type", sa.String(length=64), nullable=False)`)
- Подтверждение: apps/backend/app/models/transaction.py:25
  (`Mapped[str] = mapped_column(String(64), nullable=False)`)
- В БД нет Postgres TYPE с именем transaction_type. Валидация значений —
  только Python-side через TransactionType(StrEnum) в core/constants.py.
- Следствие: ALTER TYPE transaction_type ADD VALUE 'catcher_deposit' НЕ делаем
  (тип не существует). Python-сторона (TransactionType.CATCHER_DEPOSIT) —
  отдельной задачей Phase 1 Task 1.2.

⚠️ Bug из docs/10-deploy.md §9.2 (alembic не выполняет ALTER TYPE ADD VALUE
внутри транзакции) к этой миграции НЕ применим, потому что нет ALTER TYPE вообще.

Revision ID: 016_habit_catcher_amount
Revises: 015_checkin_status_extra_values
Create Date: 2026-08-21 11:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "016_habit_catcher_amount"
down_revision: str | None = "015_checkin_status_extra_values"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # ADDCOLUMN с DEFAULT 0 = для существующих клубов работает по-старому
    # (catcher_amount_kopecks=0 → всё в фонд при apply_catch).
    # CHECK (>= 0) — страховка на уровне БД, чтобы плохой admin endpoint
    # или баг в коде не смог записать отрицательное значение
    # (int копейки, никакого float/Decimal — см. AGENTS.md).
    op.add_column(
        "habits",
        sa.Column(
            "catcher_amount_kopecks",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_habits_catcher_amount_kopecks_nonneg",
        "habits",
        "catcher_amount_kopecks >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_habits_catcher_amount_kopecks_nonneg", "habits", type_="check")
    op.drop_column("habits", "catcher_amount_kopecks")

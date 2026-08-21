"""penalties: split columns (catcher_amount + is_suspicious_pair).

EXECUTION-PLAN-2026-08-19.md Phase 1 Task 1.4 (rebuild 2026-08-21):
разделение списания на catcher_amount (ловцу) + fund_share (в фонд).
is_suspicious_pair — флаг для лидерборда (variant A: деньги НЕ блокируются,
только метка для фильтрации фейковых поимок).

fund_share УЖЕ существует в схеме (apps/backend/app/models/penalty.py:34,
создан миграцией 001_initial_schema.py) и хранит фактически списанное в фонд
после клэмп-ДО в apply_catch (penalty_service.py:206: add_to_prize_pool(amount)).
Переиспользуем fund_share, не дублируем колонку fund_amount — это сэкономит
миграцию и снижает риск расхождений.

В Phase 8 (cleanup bonus) можно переименовать fund_share → fund_amount если
название покажется более понятным, или оставить — это просто имя колонки.

DEFAULT 0 / false:
- Для существующих penalties на проде поведение не меняется: amount = fund_share,
  catcher_amount = 0 (бонусная механика отключена после Task 1.3, новая ещё
  не пишется), is_suspicious_pair = false (variant A — деньги не блокируются).
- На проде сейчас 0 строк в penalties (никто не был пойман), backfill не нужен,
  но DEFAULT покрывает любые исторические данные в тестовых средах.

Revision ID: 017_penalty_split_columns
Revises: 016_habit_catcher_amount
Create Date: 2026-08-21 11:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "017_penalty_split_columns"
down_revision: str | None = "016_habit_catcher_amount"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # catcher_amount — сколько ушло ловцу на депозит (Task 1.3).
    # = 0 для существующих строк (бонусная механика не писала деньги ловцу).
    op.add_column(
        "penalties",
        sa.Column(
            "catcher_amount",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # is_suspicious_pair — флаг для лидерборда (variant A, см. Task 1.3).
    # Деньги НЕ блокируются для flagged пар (сговор финансово невыгоден),
    # но лидерборд фильтрует такие поимки из метрик catches_count.
    op.add_column(
        "penalties",
        sa.Column(
            "is_suspicious_pair",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # CHECK constraint для catcher_amount: int копейки, никакого float/Decimal
    # и не может быть отрицательным. fund_share уже имеет CHECK в 001_initial.
    op.create_check_constraint(
        "ck_penalties_catcher_amount_nonneg",
        "penalties",
        "catcher_amount >= 0",
    )
    # CHECK constraint для инварианта суммы:
    # amount = catcher_amount + fund_share (фактически списанное = доли).
    # Это страховка от расхождений в Task 1.3 (если кто-то забудет одну из долей).
    op.create_check_constraint(
        "ck_penalties_amount_equals_sum",
        "penalties",
        "amount = catcher_amount + fund_share",
    )


def downgrade() -> None:
    op.drop_constraint("ck_penalties_amount_equals_sum", "penalties", type_="check")
    op.drop_constraint("ck_penalties_catcher_amount_nonneg", "penalties", type_="check")
    op.drop_column("penalties", "is_suspicious_pair")
    op.drop_column("penalties", "catcher_amount")
    # fund_share не трогаем — часть оригинальной схемы (001_initial_schema.py).

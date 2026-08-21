"""Phase 8 (cleanup bonus mechanics, 2026-08-21).

EXECUTION-PLAN-2026-08-19.md Phase 8: полное удаление виртуальной
бонусной механики из Phase 0/1 (BonusService, apply_catch_bonus,
expire_bonus_points, integrity_check_bonus_transactions).

Что удаляется в upgrade():
- users.bonus_points (BigInteger, DEFAULT 0)
- users.bonus_points_updated_at (TIMESTAMPTZ nullable)
- memberships.bonus_points (BigInteger, DEFAULT 0)
- penalties.catcher_bonus_points (Integer, DEFAULT 0)
- penalties.bonus_applied (Boolean, DEFAULT false)
- bonus_rules таблица (UUID + event_type + threshold + reward_type + reward_value)

⚠️ Потеря данных (принято Дмитрием 2026-08-21):
- 2 строки penalties.catcher_bonus_points=1 для юзера 𝔭𝖗𝖎𝖓𝖙
  (тестовые от 2026-08-15 и 2026-08-19). Это виртуальные очки, не рубли.
  Старая механика не работает на проде (recon #1, apply_catch_bonus
  не вызывается). Потеря не имеет финансового значения.

⚠️ НЕ ТРОГАЕМ в этой миграции:
- transactions.type VARCHAR — исторические строки с type='bonus_*'
  (на проде их 0) НЕ удаляются, тип-константы из Python enum не мешают.
- TransactionType.CATCHER_DEPOSIT (новая механика) — отдельный enum-член.
- penalties.catcher_amount / fund_share / is_suspicious_pair — новая механика.
- users / memberships / penalties / habits — остальные колонки не задеты.

⚠️ Downgrade = ТОЛЬКО СТРУКТУРА (rollbackable):
Возвращаются колонки с DEFAULT-значениями и пустая таблица bonus_rules.
Исторические данные (bonus_points для юзеров, bonus_rules записи,
catcher_bonus_points для пенальти) НЕ восстанавливаются.
Это явное решение: rollback восстанавливает работоспособность кода,
но не финансовые/виртуальные балансы. make migrate-test round-trip
(upgrade head → downgrade base → upgrade head) проходит.

Структура bonus_rules в downgrade() взята ТОЧНО из миграции 002
(apps/backend/alembic/versions/002_bonus_and_penalty_fixes.py):
postgresql.UUID + event_type VARCHAR(64) + threshold INTEGER +
reward_type VARCHAR(64) + reward_value INTEGER.

Revision ID: 018_drop_bonus_mechanics
Revises: 017_penalty_split_columns
Create Date: 2026-08-21 14:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


revision: str = "018_drop_bonus_mechanics"
down_revision: str | None = "017_penalty_split_columns"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # penalties.* — удаляем в порядке: сначала bonus_applied (зависит
    # концептуально от catcher_bonus_points, не FK), потом catcher_bonus_points.
    # В Postgres нет FK между ними, но порядок документирован в миграции 002
    # (drop в обратном порядке от create).
    op.drop_column("penalties", "bonus_applied")
    op.drop_column("penalties", "catcher_bonus_points")

    # bonus_rules таблица — пустая (0 строк), DROP TABLE безопасен.
    op.drop_table("bonus_rules")

    # memberships.bonus_points (BigInteger DEFAULT 0 — было перенесено в
    # users.bonus_points миграцией 003, потом DEPRECATED в Phase 1).
    op.drop_column("memberships", "bonus_points")

    # users.bonus_points + bonus_points_updated_at.
    op.drop_column("users", "bonus_points_updated_at")
    op.drop_column("users", "bonus_points")


def downgrade() -> None:
    # ROLLBACKABLE: восстанавливаем только структуру (колонки с DEFAULT,
    # пустую таблицу). Данные не возвращаются — это явное решение,
    # принятое в Phase 8 recon (2026-08-21, см. EXECUTION-PLAN §Phase 8).

    # 1. users.bonus_points + bonus_points_updated_at.
    op.add_column(
        "users",
        sa.Column(
            "bonus_points",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "bonus_points_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # 2. memberships.bonus_points.
    op.add_column(
        "memberships",
        sa.Column(
            "bonus_points",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # 3. penalties.catcher_bonus_points + bonus_applied.
    op.add_column(
        "penalties",
        sa.Column(
            "catcher_bonus_points",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "penalties",
        sa.Column(
            "bonus_applied",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # 4. bonus_rules таблица — структура ТОЧНО из миграции 002.
    op.create_table(
        "bonus_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),  # noqa: F821 (alembic op)
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("reward_type", sa.String(length=64), nullable=False),
        sa.Column("reward_value", sa.Integer(), nullable=False),
    )

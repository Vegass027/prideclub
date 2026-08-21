"""user_stats + user_statuses + stat_definitions + habits.stat_definition_id.

Phase 3 v2 (per Дмитрий 2026-08-21): global characteristics per
user_id + stat_definition_id, NOT per-club. Source of truth for
stat names — новый справочник `stat_definitions` (8 канонических).

Полная спецификация: `TZ_kharakteristiki_personazha_v2.md` §0, §1, §2, §3,
план имплементации — `EXECUTION-PLAN-2026-08-19.md` snapshot от 21.08.2026.

В одной миграции:

1. CREATE TABLE stat_definitions + bulk INSERT 8 canonical seeds.
   - `slug` UNIQUE — внутренний ключ (FK из habits).
   - `name` UNIQUE — отображаемое имя; гарантирует однозначность
     generic backfill (`WHERE h.stat_name = sd.name`). Без UNIQUE
     дубль `Интеллект` сделал бы JOIN неоднозначным.

2. CREATE TABLE user_statuses + bulk INSERT 5 emoji seed rows.
   `icon` VARCHAR(16) NOT NULL — storage guard, без CHECK на
   длину (per Дмитрий 21.08.2026: emoji могут быть multi-char с
   ZWJ, business-проверки UI всё равно контролируют).

3. ALTER TABLE habits:
   - ADD COLUMN stat_definition_id (NULLABLE; FK → stat_definitions
     ON DELETE RESTRICT);
   - FK constraint fk_habits_stat_definition_id_stat_definitions;
   - partial index ix_habits_stat_definition WHERE NOT NULL.

4. Generic backfill (per Дмитрий 21.08.2026) — точное совпадение
   `habits.stat_name = stat_definitions.name`:

       UPDATE habits AS h
       SET stat_definition_id = sd.id
       FROM stat_definitions AS sd
       WHERE h.stat_name = sd.name;

   Корректен благодаря UNIQUE(name) на stat_definitions. Любой
   текущий или будущий клуб с matching stat_name получит FK
   автоматически. Неканонические значения (`Дисциплина`, что-то
   экзотическое) остаются NULL — админ re-picks через обязательный
   UI-баннер (Task 3.8).

5. CREATE TABLE user_stats:
   - UNIQUE (user_id, stat_definition_id) — критично;
   - FK на users и stat_definitions (оба RESTRICT);
   - 3 CHECK (value>=0, frozen-↔-frozen_at, …);
   - 3 индекса: ix_user_stats_user,
                ix_user_stats_stat_value (DESC для лидерборда),
                ix_user_stats_freeze_cron (partial для cron).

⚠️ NOT NULL на habits.stat_definition_id НЕ ставим в этой
миграции — существующие клубы с `Дисциплина` остаются
NULL до явного admin re-pick. Отдельная миграция 020
(после деплоя Task 3.8 на прод + проверка
`count(*) WHERE stat_definition_id IS NULL = 0`).

⚠️ habits.stat_name и habits.stat_icon НЕ удаляются — отдельная
миграция 021 в Phase 5/6 (Task 3.12, отдельно от Phase 3).

⚠️ transactions.type остаётся VARCHAR(64), как в Phase 1:
новые enum-значения НЕ нужны (stat_value НЕ пишется в
transactions; это отдельная ось, не деньги).

⚠️ `updated_at` в трёх новых моделях обновляется через SQLAlchemy
`onupdate=func.now()` в модели, не через Postgres trigger в
этой миграции (per Дмитрий 21.08.2026).

Revision ID: 019_user_stats_and_statuses
Revises: 018_drop_bonus_mechanics
Create Date: 2026-08-21 16:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "019_user_stats_and_statuses"
down_revision: str | None = "018_drop_bonus_mechanics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. stat_definitions — справочник из 8 канонических характеристик.
    # `slug` — внутренний неизменяемый ключ (FK из habits); `name` UNIQUE —
    # отображаемое имя, гарантирует однозначность backfill-join'а.
    op.create_table(
        "stat_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("icon", sa.String(16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z][a-z0-9_]*$'",
            name="ck_stat_definitions_slug_format",
        ),
    )

    stat_def_table = sa.table(
        "stat_definitions",
        sa.column("slug", sa.String),
        sa.column("name", sa.String),
        sa.column("icon", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        stat_def_table,
        [
            {"slug": "intelligence", "name": "Интеллект",    "icon": "🧠", "sort_order": 1},
            {"slug": "strength",     "name": "Сила",          "icon": "💪", "sort_order": 2},
            {"slug": "endurance",    "name": "Выносливость",  "icon": "🫁", "sort_order": 3},
            {"slug": "balance",      "name": "Баланс",        "icon": "🧘", "sort_order": 4},
            {"slug": "energy",       "name": "Энергия",       "icon": "✨", "sort_order": 5},
            {"slug": "focus",        "name": "Фокус",         "icon": "🎯", "sort_order": 6},
            {"slug": "creativity",   "name": "Творчество",    "icon": "🎨", "sort_order": 7},
            {"slug": "connections",  "name": "Связи",         "icon": "🤝", "sort_order": 8},
        ],
    )

    # 2. user_statuses — 5 ступеней с emoji icon (NOT icon_url).
    op.create_table(
        "user_statuses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("status_name", sa.String(64), nullable=False, unique=True),
        sa.Column("min_threshold", sa.Integer(), nullable=False),
        sa.Column("icon", sa.String(16), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "min_threshold >= 0",
            name="ck_user_statuses_threshold_nonneg",
        ),
    )

    user_status_table = sa.table(
        "user_statuses",
        sa.column("status_name", sa.String),
        sa.column("min_threshold", sa.Integer),
        sa.column("icon", sa.String),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(
        user_status_table,
        [
            {"status_name": "На старте",   "min_threshold": 0,   "icon": "🐣", "sort_order": 1},
            {"status_name": "В потоке",    "min_threshold": 30,  "icon": "🌊", "sort_order": 2},
            {"status_name": "На волне",    "min_threshold": 100, "icon": "⚡", "sort_order": 3},
            {"status_name": "В форме",     "min_threshold": 300, "icon": "🔥", "sort_order": 4},
            {"status_name": "Режим зверя", "min_threshold": 700, "icon": "🐺", "sort_order": 5},
        ],
    )

    # 3. ALTER TABLE habits — nullable FK на stat_definitions.
    op.add_column(
        "habits",
        sa.Column(
            "stat_definition_id",
            postgresql.UUID(as_uuid=False),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_habits_stat_definition_id_stat_definitions",
        "habits",
        "stat_definitions",
        ["stat_definition_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_habits_stat_definition",
        "habits",
        ["stat_definition_id"],
        postgresql_where=sa.text("stat_definition_id IS NOT NULL"),
    )

    # 4. Generic backfill (per Дмитрий 21.08.2026) — точное совпадение
    # `habits.stat_name = stat_definitions.name` (case-sensitive).
    # Корректен благодаря UNIQUE(name) на stat_definitions: каждый
    # habit получит ровно один (или ноль) stat_definition_id.
    op.execute(
        sa.text(
            """
            UPDATE habits AS h
            SET stat_definition_id = sd.id
            FROM stat_definitions AS sd
            WHERE h.stat_name = sd.name
            """
        )
    )

    # 5. user_stats — глобальный per (user_id, stat_definition_id).
    op.create_table(
        "user_stats",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "stat_definition_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column(
            "value",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_checkin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_frozen",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_reason_text", sa.String(256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_stats_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stat_definition_id"],
            ["stat_definitions.id"],
            name="fk_user_stats_stat_definition_id_stat_definitions",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "user_id",
            "stat_definition_id",
            name="uq_user_stats_user_stat_definition",
        ),
        sa.CheckConstraint(
            "value >= 0",
            name="ck_user_stats_value_nonneg",
        ),
        sa.CheckConstraint(
            "(is_frozen = false AND frozen_at IS NULL) OR "
            "(is_frozen = true AND frozen_at IS NOT NULL)",
            name="ck_user_stats_frozen_consistent",
        ),
    )
    op.create_index("ix_user_stats_user", "user_stats", ["user_id"])
    op.create_index(
        "ix_user_stats_stat_value",
        "user_stats",
        ["stat_definition_id", sa.text("value DESC")],
    )
    op.create_index(
        "ix_user_stats_freeze_cron",
        "user_stats",
        ["stat_definition_id", "last_checkin_at"],
        postgresql_where=sa.text(
            "is_frozen = false AND last_checkin_at IS NOT NULL"
        ),
    )


def downgrade() -> None:
    # Reverse order: most-dependent first.
    op.drop_index("ix_user_stats_freeze_cron", table_name="user_stats")
    op.drop_index("ix_user_stats_stat_value", table_name="user_stats")
    op.drop_index("ix_user_stats_user", table_name="user_stats")
    op.drop_table("user_stats")

    # Revert ALTER habits.
    op.drop_index("ix_habits_stat_definition", table_name="habits")
    op.drop_constraint(
        "fk_habits_stat_definition_id_stat_definitions",
        "habits",
        type_="foreignkey",
    )
    op.drop_column("habits", "stat_definition_id")

    # Drop seeded lookups.
    op.drop_table("user_statuses")
    op.drop_table("stat_definitions")

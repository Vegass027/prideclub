"""habits: proof_types — массив разрешённых типов чек-ина.

Revision ID: 012_proof_types
Revises: 011_habit_chat_topic
Create Date: 2026-07-23 13:00:00.000000

Зачем:
    Раньше у клуба был ровно один `proof_type` (video_note | photo | text).
    Это неудобно: владелец не мог разрешить «кружок ИЛИ фото».
    Заменяем на `proof_types: JSONB` — массив 1..3 значений.
    `proof_type` остаётся для обратной совместимости и денормализации
    (всегда равен `proof_types[0]`, выставляется триггером/app-кодом).

Конвенция:
    proof_types:
        - список из 1..3 строк, каждая ∈ {"video_note", "photo", "text"};
        - дубликаты запрещены;
        - пустой массив запрещён (NOT NULL DEFAULT '["video_note"]' но
          CHECK приложение валидирует, см. HabitService).

Совместимость:
    - Существующие строки получают `proof_types = [proof_type]` (бэкфилл).
    - `proof_type` не дропаем — оставлен для существующих клиентов
      (Bot API, Mini App), обновляется синхронно при изменении
      `proof_types` (первый элемент).
    - Чек-ин логика (CheckinService.process_checkin) теперь проверяет
      `proof.proof_type.value in habit.proof_types` вместо `==`.

Downgrade:
    Дроп колонки `proof_types`. Данные теряются, но `proof_type`
    остаётся и продолжает работать как раньше. Используйте только
    если фронт/бот откачены.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "012_proof_types"
down_revision: str | None = "011_habit_chat_topic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Добавляем колонку как nullable, чтобы пройти на любых строках.
    op.add_column(
        "habits",
        sa.Column(
            "proof_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Массив разрешённых типов чек-ина "
                "(1..3 значений: video_note/photo/text)."
            ),
        ),
    )

    # 2. Бэкфилл: конвертируем существующий proof_type (Enum) в массив строк.
    #    Enum хранится как VARCHAR со значением типа 'video_note' — берём .text.
    op.execute(
        sa.text(
            "UPDATE habits SET proof_types = jsonb_build_array(proof_type::text) "
            "WHERE proof_types IS NULL"
        )
    )

    # 3. Делаем NOT NULL — после бэкфилла все строки имеют значение.
    op.alter_column("habits", "proof_types", nullable=False)

    # 4. CHECK constraint: массив 1..3 элементов.
    #    PostgreSQL запрещает subqueries в CHECK constraints, поэтому
    #    валидацию значений и уникальности делает приложение
    #    (HabitService._validate_proof_types + Pydantic model_validator).
    #    Здесь только структурный constraint.
    op.create_check_constraint(
        constraint_name="ck_habits_proof_types_valid",
        table_name="habits",
        condition=(
            "jsonb_typeof(proof_types) = 'array' "
            "AND jsonb_array_length(proof_types) BETWEEN 1 AND 3"
        ),
    )

    # 5. GIN-индекс для быстрого поиска клубов, принимающих конкретный тип.
    #    Используется в будущих эндпоинтах (например, выбор клубов по
    #    предпочтению пользователя) и для админских фильтров.
    op.create_index(
        "ix_habits_proof_types_gin",
        "habits",
        ["proof_types"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_habits_proof_types_gin", table_name="habits")
    op.drop_constraint("ck_habits_proof_types_valid", "habits", type_="check")
    op.drop_column("habits", "proof_types")
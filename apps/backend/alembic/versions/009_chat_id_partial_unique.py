"""habits: chat_id partial unique index — несколько клубов с chat_id=0

Revision ID: 009_chat_id_partial_unique
Revises: 008_character_and_club_fields
Create Date: 2026-07-22 21:10:00.000000

Конвенция проекта (см. AGENTS.md, schemas/__init__.py:172):
    chat_id == 0 — клуб без привязки к Telegram-группе
    chat_id != 0 — клуб привязан к чату бота

В исходной миграции 001_initial_schema.py на колонке стоял полный UNIQUE,
что не даёт хранить несколько клубов в «не привязанном» состоянии
(`chat_id=0`) одновременно. Это сломало бы автоматическое обнуление мёртвых
chat_id в эндпоинте /admin/v1/habits/available_chats на втором клубе
подряд.

Решение: НЕ трогаем nullability колонки (проект везде опирается на
`chat_id == 0` как «не привязан»). Снимаем автогенерированный UNIQUE
constraint, создаём partial unique index, который запрещает дубликаты
только среди ненулевых (≠0) chat_id. Строки с chat_id=0 могут
сосуществовать без ограничений.

Не задевает:
    - `habit.chat_id == 0` сравнения во всём коде (конвенция сохранена);
    - `payload.chat_id` от Telegram (никогда не 0);
    - `repo.get_by_chat_id(0)` — никто не ищет «0» как валидный чат.

Data safety:
    - downgrade удаляет partial index, пересоздаёт полный UNIQUE. Если в
      этот момент в БД более одной строки с chat_id=0 — downgrade упадёт
      на уникальности. Перед rollback-ом рекомендуется сначала привязать
      лишние «нулевые» клубы к новым чатам или удалить.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "009_chat_id_partial_unique"
down_revision: Union[str, None] = "008_character_and_club_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Снимаем автогенерированный UNIQUE constraint (имя habits_chat_id_key — дефолт PG).
    op.drop_constraint("habits_chat_id_key", "habits", type_="unique")

    # 2. Partial unique index: дубликаты только среди chat_id != 0.
    op.create_index(
        "ix_habits_chat_id_active",
        "habits",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("chat_id <> 0"),
    )


def downgrade() -> None:
    op.drop_index("ix_habits_chat_id_active", table_name="habits")
    op.create_unique_constraint("habits_chat_id_key", "habits", ["chat_id"])

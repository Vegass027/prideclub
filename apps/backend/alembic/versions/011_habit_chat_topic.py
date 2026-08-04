"""habits: chat_topic_thread_id — топик общего чата клуба.

Revision ID: 011_habit_chat_topic
Revises: 010_habit_topics
Create Date: 2026-07-23 02:30:00.000000

Зачем:
    В супергруппе с включённым режимом топиков (forum mode) клуб
    помимо топика чек-инов и топика уведомлений может иметь
    отдельный топик для переписки участников ("чат клуба").
    Чтобы кнопка 'Перейти в чат' в User Mini App открывала именно
    этот топик, храним message_thread_id.

Конвенция:
    NULL              — топик чата не задан. Кнопка 'Перейти в чат'
                        не показывается либо ведёт в General чата.
    <int>             — message_thread_id топика форума; формат ссылки
                        пользователя: https://t.me/c/<chat_id>/<thread_id>.

Совместимость:
    - Существующие клубы остаются с NULL. Старые ссылки на корневой
      чат (chat_id) не интерпретируются — топик не задан.
    - Index — partial btree по chat_topic_thread_id (используется в
      фильтрации). Уникальность НЕ нужна: один и тот же топик нельзя
      привязать к двум клубам одновременно — это валидируется на
      уровне HabitService, не на уровне БД.

Data safety:
    - downgrade просто дропает колонку и индекс. Значения теряются,
      поэтому перед rollback-ом стоит убедиться, что колонка не
      используется.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011_habit_chat_topic"
down_revision: str | None = "010_habit_topics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "habits",
        sa.Column(
            "chat_topic_thread_id",
            sa.BigInteger(),
            nullable=True,
            comment="message_thread_id топика форума для общего чата участников клуба.",
        ),
    )
    op.create_index(
        "ix_habits_chat_topic",
        "habits",
        ["chat_topic_thread_id"],
        postgresql_where=sa.text("chat_topic_thread_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_habits_chat_topic", table_name="habits")
    op.drop_column("habits", "chat_topic_thread_id")
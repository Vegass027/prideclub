"""habits: checkin_topic_thread_id + notifications_topic_thread_id.

Revision ID: 010_habit_topics
Revises: 009_chat_id_partial_unique
Create Date: 2026-07-23 00:55:00.000000

Зачем:
    Супергруппа с включённым режимом топиков (forum mode) может содержать
    несколько топиков. Чтобы бот принимал чек-ины только из конкретного
    топика и публиковал уведомления о ловле в отдельном топике, храним
    два message_thread_id.

Конвенция (см. AGENTS.md, TZ §topic-scoped):
    NULL              — топик не задан; чек-ины принимаются в старом режиме
                        (любое сообщение в чате клуба), уведомления
                        публикуются в General (без thread_id).
    <int>             — message_thread_id топика в Telegram Bot API;
                        в payload от бота поле message_thread_id
                        сравнивается с checkin_topic_thread_id;
                        публикация в notifications_topic_thread_id.

Совместимость:
    - Существующие клубы остаются с NULL (старое поведение — режим
      "без топиков" совпадает с поведением до этой миграции).
    - Index — частичный btree по каждому из полей (используется
      в фильтрации). Уникальность НЕ нужна: один и тот же топик нельзя
      привязать к двум клубам одновременно — это валидируется на уровне
      HabitService (get_by_chat_and_thread), не на уровне БД.

Data safety:
    - downgrade просто дропает колонки и индексы. Значения теряются,
      поэтому перед rollback-ом стоит убедиться, что колонки не
      используются.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "010_habit_topics"
down_revision: Union[str, None] = "009_chat_id_partial_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "habits",
        sa.Column(
            "checkin_topic_thread_id",
            sa.BigInteger(),
            nullable=True,
            comment="message_thread_id топика форума, из которого бот принимает чек-ины.",
        ),
    )
    op.add_column(
        "habits",
        sa.Column(
            "notifications_topic_thread_id",
            sa.BigInteger(),
            nullable=True,
            comment="message_thread_id топика форума, в который публикуются уведомления о ловле и штрафах.",
        ),
    )
    op.create_index(
        "ix_habits_checkin_topic",
        "habits",
        ["checkin_topic_thread_id"],
        postgresql_where=sa.text("checkin_topic_thread_id IS NOT NULL"),
    )
    op.create_index(
        "ix_habits_notifications_topic",
        "habits",
        ["notifications_topic_thread_id"],
        postgresql_where=sa.text("notifications_topic_thread_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_habits_notifications_topic", table_name="habits")
    op.drop_index("ix_habits_checkin_topic", table_name="habits")
    op.drop_column("habits", "notifications_topic_thread_id")
    op.drop_column("habits", "checkin_topic_thread_id")
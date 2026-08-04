"""habits: character & club extended fields (TZ §2.1).

Revision ID: 008_character_and_club_fields
Revises: 007_habit_admin_fields
Create Date: 2026-07-21 22:10:00.000000

Добавляет расширенные клубные поля, которые используются и админским флоу
(Фаза A — `telegram_invite_link`, `photo_url`), и модулем «Персонаж и
характеристики» (Фаза B — `stat_name`, `stat_icon`, `stat_gain_per_checkin`,
`stat_loss_per_miss`, `member_limit`, `curator_id`).

Backfill: для существующих клубов автоматически ставятся
`stat_name = 'Дисциплина'`, `stat_gain_per_checkin = 2`, `stat_loss_per_miss = 1`
через server_default — NOT NULL достигается одной командой ALTER без отдельного UPDATE.

CHECK constraints гарантируют инварианты на уровне БД (дополнительная страховка
к валидации в HabitService).

ix_habits_curator — частичный индекс по curator_id, обслуживает будущий
`GET /admin/v1/habits?curator_id=...` (если владелец захочет видеть клубы по куратору).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008_character_and_club_fields"
down_revision: str | None = "007_habit_admin_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("habits", sa.Column("photo_url", sa.String(length=512), nullable=True))
    op.add_column(
        "habits",
        sa.Column("telegram_invite_link", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "habits",
        sa.Column(
            "stat_name",
            sa.String(length=64),
            nullable=False,
            server_default="Дисциплина",
        ),
    )
    op.add_column("habits", sa.Column("stat_icon", sa.String(length=16), nullable=True))
    op.add_column(
        "habits",
        sa.Column(
            "stat_gain_per_checkin",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )
    op.add_column(
        "habits",
        sa.Column(
            "stat_loss_per_miss",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column("habits", sa.Column("member_limit", sa.Integer(), nullable=True))
    op.add_column(
        "habits",
        sa.Column("curator_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_habits_curator_id_users",
        "habits",
        "users",
        ["curator_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "habits_stat_loss_positive",
        "habits",
        "stat_loss_per_miss > 0",
    )
    op.create_check_constraint(
        "habits_stat_gain_positive",
        "habits",
        "stat_gain_per_checkin > 0",
    )
    op.create_check_constraint(
        "habits_member_limit_positive",
        "habits",
        "member_limit IS NULL OR member_limit > 0",
    )
    op.create_index(
        "ix_habits_curator",
        "habits",
        ["curator_id"],
        postgresql_where=sa.text("curator_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_habits_curator", table_name="habits")
    op.drop_constraint("habits_member_limit_positive", "habits", type_="check")
    op.drop_constraint("habits_stat_gain_positive", "habits", type_="check")
    op.drop_constraint("habits_stat_loss_positive", "habits", type_="check")
    op.drop_constraint("fk_habits_curator_id_users", "habits", type_="foreignkey")
    op.drop_column("habits", "curator_id")
    op.drop_column("habits", "member_limit")
    op.drop_column("habits", "stat_loss_per_miss")
    op.drop_column("habits", "stat_gain_per_checkin")
    op.drop_column("habits", "stat_icon")
    op.drop_column("habits", "stat_name")
    op.drop_column("habits", "telegram_invite_link")
    op.drop_column("habits", "photo_url")

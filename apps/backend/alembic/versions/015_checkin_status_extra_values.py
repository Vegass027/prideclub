"""checkin_status: ADD VALUE 'joined_late' + 'caught' (Pravki-bug-fixes §Z-19 + §Z-21).

Pravki-subscribe-and-join.md §Z-19 (bug 3 — joiner-late protection):
    'joined_late' — статус для пользователя, вступившего в клуб в день
    поимки ПОСЛЕ закрытия checkin_window. Защита от ловли в день вступления:
    can_catch автоматически False (status != 'missed').

Pravki-subscribe-and-join.md §Z-21 (bug 1 — caught badge):
    'caught' — статус для пользователя, пойманного в этот день.
    PenaltyService.apply_catch пишет Checkin(status='caught') при успешной
    поимке. can_catch в /members автоматически False (status != 'missed').

PostgreSQL ALTER TYPE ADD VALUE:
- IF NOT EXISTS — идемпотентность при retry миграции.
- Нельзя использовать в одной транзакции с другим DDL (миграция содержит
  ТОЛЬКО эти два op.execute).
- В PostgreSQL 16 ADD VALUE не поддерживает откат (downgrade пустой) —
  см. https://www.postgresql.org/docs/16/sql-altertype.html.
  Это документированное ограничение PG, не баг миграции.

Revision ID: 015_checkin_status_extra_values
Revises: 014b_drop_membership_dep
Create Date: 2026-08-09 13:05:00.000000
"""
from __future__ import annotations

from alembic import op


revision: str = "015_checkin_status_extra_values"
down_revision: str | None = "014b_drop_membership_dep"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    # IF NOT EXISTS для идемпотентности. Оба значения атомарны с точки зрения
    # схемы (не пересекаются по логике и не требуют ordering между собой).
    # Python CheckinStatus enum обновляется в коде ОДНОВРЕМЕННО с этой
    # миграцией (apps/backend/app/core/constants.py).
    op.execute("ALTER TYPE checkin_status ADD VALUE IF NOT EXISTS 'joined_late'")
    op.execute("ALTER TYPE checkin_status ADD VALUE IF NOT EXISTS 'caught'")


def downgrade() -> None:
    # PostgreSQL 16 не поддерживает ALTER TYPE ... DROP VALUE.
    # Если потребуется откатить enum — это ручная миграция с
    # pg_type пересозданием, делается отдельно. См. комментарий в шапке файла.
    pass

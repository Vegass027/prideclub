"""users: photo_file_id для подхода C' (см. Pravki.md §7.1).

Подход C' (безопасный):
    Backend НЕ хранит само фото и НЕ хранит file_path (временный, TTL 1ч на CDN).
    Храним только photo_file_id (постоянный между вызовами, см.
    https://core.telegram.org/bots/faq#can-i-count-on-file_ids-to-be-persistent).

    Endpoint GET /api/v1/users/{id}/photo → 307 redirect на Telegram CDN.
    Токен бота остаётся server-side (не попадает в JSON клиента).
    file_path получается через bot.getFile и кэшируется в Redis на 6ч.

Зачем:
    Для лидерборда нужны аватарки участников. Mini App SDK даёт photo_url
    только для текущего юзера (window.Telegram.WebApp.initDataUnsafe.user.photo_url).
    Для других участников — только через Telegram Bot API.

Нагрузка:
    Cron worker раз в сутки обновляет photo_file_id для активных
    пользователей (status=ACTIVE memberships). На 1000 users = 2000 req
    к Bot API в сутки = 0.023 req/sec — в 1300 раз ниже глобального
    лимита 30 req/sec (см. core.telegram.org/bots/faq).

Privacy:
    photo_file_id сам по себе не PII (это opaque Telegram token),
    но всё равно ограничиваем доступ — endpoint требует TelegramUserDbDep
    (initData middleware).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013_user_photo"
down_revision: str | None = "012_proof_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "photo_file_id",
            sa.String(128),
            nullable=True,
            comment=(
                "Telegram file_id аватарки пользователя (постоянный между "
                "вызовами Bot API). NULL = нет аватарки или ещё не "
                "подтянули через cron."
            ),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "photo_fetched_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment=(
                "Когда последний раз worker обновил photo_file_id. "
                "Используется для отладки и мониторинга cron'а."
            ),
        ),
    )
    # Partial index — пользователи с фото (используется при JOIN в лидерборде)
    op.create_index(
        "ix_users_photo_file_id",
        "users",
        ["photo_file_id"],
        postgresql_where=sa.text("photo_file_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_photo_file_id", table_name="users")
    op.drop_column("users", "photo_fetched_at")
    op.drop_column("users", "photo_file_id")

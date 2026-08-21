from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.user_stats import UserStats


class StatDefinition(Base):
    """Справочник канонических характеристик (Phase 3 v2).

    Source of truth для названий статов. `habits.stat_name` остаётся
    в БД для backward-compat, но больше НЕ source of truth —
    `habits.stat_definition_id` FK → эта таблица (см. миграцию 019).

    Invariants (из миграции 019):
    - `slug` UNIQUE, regex `^[a-z][a-z0-9_]*$`. Технический стабильный
      ключ (FK из habits, не меняется после создания).
    - `name` UNIQUE. Отображаемое имя в UI. UNIQUE обязателен для
      корректности generic backfill (`UPDATE habits ... WHERE
      h.stat_name = sd.name`): без UNIQUE дубль `Интеллект` в
      справочнике сделал бы JOIN неоднозначным.
    - `icon` VARCHAR(16) NOT NULL — короткий display-emoji без обещаний
      про UTF-8 bytes / ZWJ (UI контролируется через seed + будущий
      admin endpoint, не через CHECK на длину в БД).
    - `is_active` default true — soft-delete флаг (НЕ удалять строки,
      на которые ссылаются user_stats; FK ON DELETE RESTRICT).
    - `updated_at` обновляется через `onupdate=func.now()` в любых
      ORM-операциях UPDATE (description / sort_order / is_active).
      На уровне Postgres триггера нет — обновления идут через ORM.

    Дополнительная навигация `user_stats: list[UserStats]` —
    для сервисов типа CharacterService.get_leaderboard.
    """

    __tablename__ = "stat_definitions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    icon: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Back-reference из user_stats. Используется в CharacterService
    # для лидерборда (read-only JOIN).
    user_stats: Mapped[list["UserStats"]] = relationship(
        back_populates="stat_definition"
    )

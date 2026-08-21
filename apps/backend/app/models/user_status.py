from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserStatus(Base):
    """Глобальный статус персонажа (Phase 3 v2).

    Вычисляется как `MAX(min_threshold) FROM user_statuses WHERE
    min_threshold <= SUM(user_stats.value)` для конкретного юзера.

    5 ступеней (per TZ v2 §3.1):

        🐣 На старте (0)   → 🌊 В потоке (30)   → ⚡ На волне (100)
        → 🔥 В форме (300) → 🐺 Режим зверя (700)

    Замена старого v3.0 TZ варианта с `icon_url` (он требовал CDN
    для значков — нет в MVP). `icon` VARCHAR(16) NOT NULL — короткий
    display-emoji без CHECK на длину (UI контролируется через seed +
    будущий admin endpoint).

    У `user_statuses` НЕТ колонки `updated_at`: это справочник,
    практически статичный после seed (per Дмитрий 21.08.2026).
    Если когда-то понадобится UPDATE (например, смена emoji →
    real icon) — добавится отдельной миграцией с `onupdate`.

    Update emoji → real icon image = просто UPDATE этой таблицы,
    код CharacterService.calculate_status не меняется.
    """

    __tablename__ = "user_statuses"
    __table_args__ = (
        CheckConstraint(
            "min_threshold >= 0", name="ck_user_statuses_threshold_nonneg"
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    status_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    min_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    icon: Mapped[str] = mapped_column(String(16), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

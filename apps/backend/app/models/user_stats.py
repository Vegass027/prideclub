from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.stat_definition import StatDefinition


class UserStats(Base):
    """Глобальный per-user-per-character счётчик (Phase 3 v2).

    UNIQUE (user_id, stat_definition_id) — критично: два клуба с одной
    stat пишут в ОДНУ строку. Статы качаются из ЛЮБОГО клуба с этой
    характеристикой, не изолированно.

    Freeze (per TZ v2 §4):
    - is_frozen=true ↔ frozen_at IS NOT NULL (CHECK + bi-conditional).
    - 30 дней без чек-ина в ЛЮБОМ клубе с этой stat → cron
      (Task 3.5) ставит is_frozen=true. Возврат автоматический
      при следующем чек-ине (Task 3.4).
    - `last_checkin_at` обновляется при чек-ине в любом клубе с
      соответствующим stat_definition_id.

    ⚠️ Это ДРУГАЯ ось, не деньги. Не путать с Phase 1
    (catcher_deposit). stat_value НЕ пишется в `transactions`,
    не используется в `deposit_balance`, не связано с `penalty_amount`.

    ⚠️ `membership.status = 'left'` НЕ удаляет эту строку —
    характеристика пользователя глобальная, переживает leave.
    Re-join того же юзера в тот же клуб восстанавливает
    декремент/инкремент на ту же строку.

    `updated_at` обновляется через `onupdate=func.now()` при любых
    ORM-операциях UPDATE (value / is_frozen / last_checkin_at /
    frozen_reason_text). На уровне Postgres триггера нет.
    """

    __tablename__ = "user_stats"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "stat_definition_id",
            name="uq_user_stats_user_stat_definition",
        ),
        CheckConstraint("value >= 0", name="ck_user_stats_value_nonneg"),
        CheckConstraint(
            "(is_frozen = false AND frozen_at IS NULL) OR "
            "(is_frozen = true AND frozen_at IS NOT NULL)",
            name="ck_user_stats_frozen_consistent",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stat_definition_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("stat_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    value: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    last_checkin_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_frozen: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    frozen_reason_text: Mapped[str | None] = mapped_column(
        String(256), nullable=True
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

    # JOIN для лидерборда и для UI профиля. НЕ relationship на User
    # (нет запросов `User → UserStats` в MVP; добавим в Phase 5 если
    # потребуется).
    stat_definition: Mapped["StatDefinition"] = relationship(
        back_populates="user_stats"
    )

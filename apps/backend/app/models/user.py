from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.membership import Membership
    from app.models.transaction import Transaction


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Europe/Moscow"
    )

    # Pravki-deposit-sse.md §Z-2.1: глобальный депозит на пользователя (в копейках).
    # Общий для всех клубов. Списание и пополнение — через этот баланс, не через
    # memberships.deposit_balance (то поле удалено миграцией 014b).
    deposit_balance: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    # Pravki.md §7.1: Telegram file_id аватарки для подхода C'
    # (307 redirect на Telegram CDN). NULL = нет аватарки или worker
    # ещё не подтянул. Endpoint /api/v1/users/{id}/photo → 404 если NULL.
    photo_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    photo_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    accepted_offer_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_anonymized: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    memberships: Mapped[list[Membership]] = relationship(back_populates="user")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="user")
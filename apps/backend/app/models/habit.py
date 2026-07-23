from __future__ import annotations

from datetime import date, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, String, Text, Time, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import ProofType
from app.db.session import Base


if TYPE_CHECKING:
    from app.models.membership import Membership


class Habit(Base):
    __tablename__ = "habits"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, server_default=func.gen_random_uuid())
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)

    checkin_window_start: Mapped[time] = mapped_column(Time, nullable=False)
    checkin_window_end: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="Europe/Moscow")

    penalty_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    price_month: Mapped[int] = mapped_column(Integer, nullable=False)

    proof_type: Mapped[ProofType] = mapped_column(
        Enum(ProofType, name="proof_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    # Список из 1..3 значений ∈ {"video_note", "photo", "text"} (миграция 012).
    # `proof_type` выше — алиас `proof_types[0]`, обновляется синхронно
    # в HabitService.create/update. CHECK constraint и GIN-индекс —
    # в миграции 012.
    proof_types: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default='["video_note"]',
    )

    prize_pool: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    photo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    telegram_invite_link: Mapped[str | None] = mapped_column(String(512), nullable=True)

    stat_name: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Дисциплина"
    )
    stat_icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stat_gain_per_checkin: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )
    stat_loss_per_miss: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    member_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    curator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    checkin_topic_thread_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    notifications_topic_thread_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    chat_topic_thread_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="habit",
        passive_deletes=True,
    )

    def club_date(self, moment_utc: datetime) -> date:
        local = moment_utc.astimezone(ZoneInfo(self.timezone))
        return local.date()

    def is_within_checkin_window(self, moment_utc: datetime) -> bool:
        """Дедлайн чек-ина считается в TZ клуба, не пользователя."""
        local = moment_utc.astimezone(ZoneInfo(self.timezone))
        return self.checkin_window_start <= local.time() <= self.checkin_window_end
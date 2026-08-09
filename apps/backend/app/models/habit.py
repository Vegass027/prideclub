from __future__ import annotations

from datetime import date, datetime, time
from functools import cached_property
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

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)

    checkin_window_start: Mapped[time] = mapped_column(Time, nullable=False)
    checkin_window_end: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Europe/Moscow"
    )

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

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="habit",
        passive_deletes=True,
    )

    def club_date(self, moment_utc: datetime) -> date:
        local = moment_utc.astimezone(self.tzinfo)
        return local.date()

    @cached_property
    def tzinfo(self) -> ZoneInfo:
        """Парсит `timezone` (IANA name, например "Europe/Moscow") в ZoneInfo.

        Кэшируется на инстансе. SQLAlchemy не кэширует обычные property между
        обращениями, но @cached_property из functools хранит значение в
        `__dict__` инстанса. Для Habit-инстанса это безопасно (immutable
        после загрузки из БД; timezone колонка не меняется в runtime).
        """
        return ZoneInfo(self.timezone)

    def is_within_checkin_window(self, moment_utc: datetime) -> bool:
        """Дедлайн чек-ина считается в TZ клуба, не пользователя.

        TODO/FIXME: НЕ поддерживает окна через полночь (start > end).
        Сценарий: ночной клуб с checkin_window_start=22:00, end=06:00.
        local.time()=03:00 (внутри окна) вернёт False, потому что
        22:00 <= 03:00 = False. Текущая логика корректна ТОЛЬКО для
        нормальных окон (start <= end). Известный edge-case, обнаружен
        при работе над Pravki-bug-fixes §Z-19 (joiner-late protection).
        Новый helper `was_joined_after_window` корректно обрабатывает
        оба случая. Для фикса `is_within_checkin_window` нужен отдельный
        PR — задача выходит за скоуп Z-19 (см. план).
        """
        local = moment_utc.astimezone(self.tzinfo)
        return self.checkin_window_start <= local.time() <= self.checkin_window_end

    def was_joined_after_window(self, joined_at_utc: datetime) -> bool:
        """True если joined_at_utc в TZ клуба — после закрытия checkin_window.

        Pravki-bug-fixes §Z-19 (joiner-late protection):
        юзер вступил в клуб сегодня И после закрытия окна → joined_late.

        Корректно обрабатывает оба случая:
        1. Нормальное окно (start <= end): after_window = local.time() > end.
           Пример: окно 06:00-12:00, joined в 13:00 → True.
        2. Окно через полночь (start > end): окно покрывает [start..23:59] ∪
           [00:00..end]. Joined «после окна» = НЕ внутри дневного окна =
           time в (end, start) = time > end AND time < start.
           Пример: окно 22:00-06:00, joined в 12:00 → True (между 06:00 и 22:00).
                   joined в 23:00 → False (после start 22:00, внутри окна).

        Не вычисляем date — предполагаем что вызывающий код уже проверил
        что joined_at_utc.date() == club_date. Иначе присоединившийся
        вчера в 23:00 был бы помечен joined_late сегодня в 09:00 (False
        для нормального окна, но для окон через полночь — пересечение).
        """
        local = joined_at_utc.astimezone(self.tzinfo)
        if self.checkin_window_start <= self.checkin_window_end:
            # Нормальное окно: after = позже end.
            return local.time() > self.checkin_window_end
        # Окно через полночь: окно = [start..23:59] ∪ [00:00..end].
        # after = НЕ внутри этого объединения = (time > end) AND (time < start).
        return self.checkin_window_end < local.time() < self.checkin_window_start

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import cached_property
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, String, Text, Time, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.constants import PenaltyConfig, ProofType
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

    stat_name: Mapped[str] = mapped_column(String(64), nullable=False, server_default="Дисциплина")
    stat_icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    stat_gain_per_checkin: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    stat_loss_per_miss: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Pravki-catcher-deposit (Phase 1 Task 1.1, 2026-08-21): сумма ловцу от
    # штрафа в копейках. DEFAULT 0 = для существующих клубов поведение не
    # меняется (всё в фонд, обратная совместимость). Админ настраивает при
    # create/update клуба. Миграция 016 добавила колонку в БД, эта правка
    # синхронизирует модель. default=0 нужен для Python-конструктора
    # (Fake-тесты и seed-данные создают Habit() без явного поля — без
    # default получится None и min(None, amount) упадёт с TypeError).
    catcher_amount_kopecks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    member_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    curator_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    checkin_topic_thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    notifications_topic_thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    chat_topic_thread_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

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
        """True если момент попадает в окно чек-ина клуба (TZ клуба).

        Корректно для обоих случаев:
        1. Нормальное окно (start <= end): moment в [start, end].
           Пример: окно 09:00-21:00, now=15:00 → True.
        2. Окно через полночь (start > end): окно = [start..23:59] ∪
           [00:00..end]. Moment попадает если time >= start ИЛИ time <= end.
           Пример: окно 22:00-06:00, now=03:00 → True.
                   окно 22:00-06:00, now=12:00 → False.
                   окно 22:00-06:00, now=22:00 → True.
                   окно 22:00-06:00, now=06:00 → True (конец включительно).
                   окно 22:00-06:00, now=21:59 → False.

        Pravki-manual-catch-2026-08-18 §Шаг 1: починен FIXME из
        Pravki-business-logic-recon-2026-08-18.md #13 — раньше возвращал
        False для всех ночных клубов.
        """
        local = moment_utc.astimezone(self.tzinfo)
        local_time = local.time()
        if self.checkin_window_start <= self.checkin_window_end:
            # Нормальное окно.
            return self.checkin_window_start <= local_time <= self.checkin_window_end
        # Окно через полночь: [start..23:59] ∪ [00:00..end].
        return local_time >= self.checkin_window_start or local_time <= self.checkin_window_end

    def catch_window_end(self, club_date: date) -> datetime:
        """Граница catch window в UTC: `next_checkin_window_start − CATCH_WINDOW_BUFFER_HOURS`.

        Pravki-manual-catch-2026-08-18: catch window длится от конца окна
        чек-ина до `next_checkin_window_start − CATCH_WINDOW_BUFFER_HOURS`
        в TZ клуба. После этой границы ловить нельзя — backend отвергает
        с `CatchWindowClosedError`, UI скрывает кнопку «Поймать».

        Args:
            club_date: дата клуба для которой считается окно. Это день,
                когда юзер должен был чек-иниться. Catch window для этого
                дня может простираться в следующий клуб-день (если окно
                заканчивается поздно вечером или через полночь).

        Returns:
            datetime в UTC: `next_checkin_window_start_local − buffer_hours`.

        Корректно для обоих типов окон:
        1. Нормальное окно (start <= end): catch window для club_date
           длится от end_of_day до next day start − buffer.
        2. Окно через полночь (start > end): catch window для club_date
           длится от end_of_night до next day start − buffer
           (= следующий день в TZ клуба, время start, − buffer).

        Пример 1: окно 09:00-21:00 Europe/Moscow, club_date=2026-08-18.
            next_window_start_local = 2026-08-19 09:00 MSK
            catch_window_end_local   = 2026-08-19 07:00 MSK = 2026-08-19 04:00 UTC

        Пример 2: окно 22:00-06:00 Europe/Moscow, club_date=2026-08-18.
            next_window_start_local = 2026-08-19 22:00 MSK
            catch_window_end_local   = 2026-08-19 20:00 MSK = 2026-08-19 17:00 UTC

        Пример 3: окно 09:00-21:00 Asia/Tokyo (UTC+9), club_date=2026-08-18.
            next_window_start_local = 2026-08-19 09:00 JST
            catch_window_end_local   = 2026-08-19 07:00 JST = 2026-08-18 22:00 UTC

        Используется в `apply_catch` для проверки
        `now_utc <= habit.catch_window_end(violator.club_date)`.
        """
        buffer_hours = PenaltyConfig.CATCH_WINDOW_BUFFER_HOURS
        club_tz = self.tzinfo
        # Следующее начало окна чек-ина = club_date + 1 день в TZ клуба,
        # время start. Одинаково для нормальных окон и окон через полночь.
        next_window_start_local = datetime.combine(
            club_date + timedelta(days=1),
            self.checkin_window_start,
            tzinfo=club_tz,
        )
        catch_window_end_local = next_window_start_local - timedelta(hours=buffer_hours)
        return catch_window_end_local.astimezone(ZoneInfo("UTC"))

    def checkin_window_end_for(self, club_date: date) -> datetime:
        """Момент закрытия check-in окна в UTC для данного club_date.

        Pravki-manual-catch-2026-08-18 §Шаг 2: единая точка вычисления
        нижней границы catch window. Используется в `is_within_catch_window`
        и в UI для отображения «ловля открывается в HH:MM».

        Семантика `club_date` для разных типов окон:
        1. Нормальное окно (start <= end): club_date = дата в TZ клуба,
           когда окно открыто. Окно закрывается в `club_date HH:MM_end`.
           Пример: окно 09:00-21:00, club_date=2026-08-18
           → checkin_window_end = 2026-08-18 21:00 local.
        2. Окно через полночь (start > end): club_date = дата ОТКРЫТИЯ
           окна (вечер). Окно закрывается в `club_date+1 день HH:MM_end`.
           Пример: окно 22:00-06:00, club_date=2026-08-18
           → checkin_window_end = 2026-08-19 06:00 local (= 14ч окно).

        Это совпадает с `catch_window_end` (там тоже club_date+1 день),
        и оба они согласованы: catch window длится от
        `checkin_window_end_for(club_date)` до `catch_window_end(club_date)`.

        Returns:
            datetime в UTC: момент закрытия check-in окна.
        """
        club_tz = self.tzinfo
        if self.checkin_window_start <= self.checkin_window_end:
            # Нормальное окно: club_date = дата окна.
            local_end = datetime.combine(club_date, self.checkin_window_end, tzinfo=club_tz)
        else:
            # Окно через полночь: club_date = дата открытия (вечер),
            # окно закрывается на следующий день утром.
            local_end = datetime.combine(
                club_date + timedelta(days=1),
                self.checkin_window_end,
                tzinfo=club_tz,
            )
        return local_end.astimezone(ZoneInfo("UTC"))

    def is_within_catch_window(self, now_utc: datetime, club_date: date) -> bool:
        """Единый авторитет: True если now_utc попадает в catch window для club_date.

        Pravki-manual-catch-2026-08-18 §Шаг 2:
        catch window = (checkin_window_end_for(club_date),
                        catch_window_end(club_date)] в UTC.

        - Строгое `<` слева: во время открытого check-in окна ловить НЕЛЬЗЯ
          (юзер вправе прислать чек-ин). Пример: окно 09:00-21:00 MSK,
          club_date=2026-08-18, now=20:00 MSK = 17:00 UTC → False.
        - Нестрогое `<=` справа: последняя секунда окна ещё доступна.
          После этой секунды — CatchWindowClosedError.
        - Возвращает bool, а не бросает исключение: используется и в UI
          (флаг для скрытия кнопки), и в backend (apply_catch как
          единственная проверка).

        Контракт по now_utc: обязательно tz-aware datetime в UTC.
        Naive datetime — ProgrammingError: тихая трактовка naive как UTC
        скрывает баги (например, когда бот шлёт wall-clock без tz).
        Лучше упасть сразу в тестах/проде, чем списывать деньги «не с того
        времени».

        Используется:
        - В `PenaltyService.apply_catch` для защиты от ловли после закрытия
          catch window.
        - В `MembersApi.list_members` для `MemberRowOut.catch_window_closed`.
        - В `HabitStateResponse` (если потребуется боту).
        """
        if now_utc.tzinfo is None:
            raise ValueError(
                "is_within_catch_window requires tz-aware datetime in UTC; "
                "got naive datetime. "
                "Use datetime.now(tz=ZoneInfo('UTC')) or .replace(tzinfo=...)."
            )
        checkin_end_utc = self.checkin_window_end_for(club_date)
        catch_end_utc = self.catch_window_end(club_date)
        return checkin_end_utc < now_utc <= catch_end_utc

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

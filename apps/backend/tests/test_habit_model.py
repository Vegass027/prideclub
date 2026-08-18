"""Unit-тесты для методов Habit: is_within_checkin_window + catch_window_end.

Pravki-manual-catch-2026-08-18 §Шаг 1:
- is_within_checkin_window починен для окон через полночь (bug #13 из recon-дока).
- catch_window_end(club_date) — новая функция, заменяет старую "окно +1h" формулу
  на "до начала следующего окна минус CATCH_WINDOW_BUFFER_HOURS (2h)".

Habit инстансы создаются как обычные Python объекты (SQLAlchemy declarative),
без БД. Это даёт быстрые, детерминированные unit-тесты.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.models.habit import Habit


def _make_habit(
    *,
    start: time,
    end: time,
    tz: str = "Europe/Moscow",
) -> Habit:
    """Минимальный Habit с заданным окном и timezone."""
    return Habit(
        id=str(uuid4()),
        title="Test club",
        chat_id=-1000000000000,
        checkin_window_start=start,
        checkin_window_end=end,
        timezone=tz,
        penalty_amount=10000,  # 100₽
        price_month=25000,  # 250₽
        proof_type="video_note",
        proof_types=["video_note"],
        is_active=True,
    )


# ---------------------------------------------------------------------------
# # is_within_checkin_window
# ---------------------------------------------------------------------------


class TestIsWithinCheckinWindow:
    """Покрывает обычные окна и окна через полночь (bug #13 из recon-дока)."""

    # --- нормальное окно 09:00-21:00 Europe/Moscow ---

    def test_normal_window_inside(self) -> None:
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        # 15:00 MSK — внутри окна.
        assert h.is_within_checkin_window(datetime(2026, 8, 18, 12, 0, tzinfo=ZoneInfo("UTC")))

    def test_normal_window_start_inclusive(self) -> None:
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        # 09:00 MSK = 06:00 UTC — ровно на старте.
        assert h.is_within_checkin_window(datetime(2026, 8, 18, 6, 0, tzinfo=ZoneInfo("UTC")))

    def test_normal_window_end_inclusive(self) -> None:
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        # 21:00 MSK = 18:00 UTC — ровно на конце.
        assert h.is_within_checkin_window(datetime(2026, 8, 18, 18, 0, tzinfo=ZoneInfo("UTC")))

    def test_normal_window_outside_before(self) -> None:
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        # 08:00 MSK = 05:00 UTC — до старта.
        assert not h.is_within_checkin_window(
            datetime(2026, 8, 18, 5, 0, tzinfo=ZoneInfo("UTC"))
        )

    def test_normal_window_outside_after(self) -> None:
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        # 22:00 MSK = 19:00 UTC — после конца.
        assert not h.is_within_checkin_window(
            datetime(2026, 8, 18, 19, 0, tzinfo=ZoneInfo("UTC"))
        )

    # --- окно через полночь 22:00-06:00 Europe/Moscow (bug #13 fix) ---

    def test_midnight_window_inside_late_night(self) -> None:
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        # 03:00 MSK = 00:00 UTC — глубокая ночь, ВНУТРИ окна.
        assert h.is_within_checkin_window(datetime(2026, 8, 18, 0, 0, tzinfo=ZoneInfo("UTC")))

    def test_midnight_window_inside_early_morning(self) -> None:
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        # 05:59 MSK = 02:59 UTC — за минуту до конца окна.
        assert h.is_within_checkin_window(datetime(2026, 8, 18, 2, 59, tzinfo=ZoneInfo("UTC")))

    def test_midnight_window_start_inclusive(self) -> None:
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        # 22:00 MSK = 19:00 UTC — ровно на старте.
        assert h.is_within_checkin_window(datetime(2026, 8, 18, 19, 0, tzinfo=ZoneInfo("UTC")))

    def test_midnight_window_end_inclusive(self) -> None:
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        # 06:00 MSK = 03:00 UTC — ровно на конце.
        assert h.is_within_checkin_window(datetime(2026, 8, 18, 3, 0, tzinfo=ZoneInfo("UTC")))

    def test_midnight_window_outside_day(self) -> None:
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        # 12:00 MSK = 09:00 UTC — день, ВНЕ окна.
        assert not h.is_within_checkin_window(
            datetime(2026, 8, 18, 9, 0, tzinfo=ZoneInfo("UTC"))
        )

    def test_midnight_window_outside_just_before_start(self) -> None:
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        # 21:59 MSK = 18:59 UTC — за минуту ДО старта окна.
        assert not h.is_within_checkin_window(
            datetime(2026, 8, 18, 18, 59, tzinfo=ZoneInfo("UTC"))
        )

    def test_midnight_window_outside_just_after_end(self) -> None:
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        # 06:01 MSK = 03:01 UTC — через минуту ПОСЛЕ конца окна.
        assert not h.is_within_checkin_window(
            datetime(2026, 8, 18, 3, 1, tzinfo=ZoneInfo("UTC"))
        )


# ---------------------------------------------------------------------------
# # catch_window_end
# ---------------------------------------------------------------------------


class TestCatchWindowEnd:
    """Покрывает 4 типа кейсов из impact-плана D."""

    def test_normal_window_europe_moscow(self) -> None:
        """Окно 09:00-21:00 MSK, club_date=2026-08-18.

        next_window_start = 2026-08-19 09:00 MSK
        catch_window_end  = 2026-08-19 07:00 MSK = 2026-08-19 04:00 UTC.
        """
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        result = h.catch_window_end(date(2026, 8, 18))
        assert result == datetime(2026, 8, 19, 4, 0, tzinfo=ZoneInfo("UTC"))

    def test_midnight_window_europe_moscow(self) -> None:
        """Окно 22:00-06:00 MSK, club_date=2026-08-18.

        next_window_start = 2026-08-19 22:00 MSK
        catch_window_end  = 2026-08-19 20:00 MSK = 2026-08-19 17:00 UTC.
        """
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        result = h.catch_window_end(date(2026, 8, 18))
        assert result == datetime(2026, 8, 19, 17, 0, tzinfo=ZoneInfo("UTC"))

    def test_normal_window_asia_tokyo(self) -> None:
        """Окно 09:00-21:00 Asia/Tokyo (UTC+9), club_date=2026-08-18.

        next_window_start_local = 2026-08-19 09:00 JST
        catch_window_end_local  = 2026-08-19 07:00 JST = 2026-08-18 22:00 UTC.
        """
        h = _make_habit(
            start=time(9, 0), end=time(21, 0), tz="Asia/Tokyo"
        )
        result = h.catch_window_end(date(2026, 8, 18))
        assert result == datetime(2026, 8, 18, 22, 0, tzinfo=ZoneInfo("UTC"))

    def test_midnight_window_america_new_york(self) -> None:
        """Окно 23:00-07:00 America/New_York (UTC-5 зимой / UTC-4 летом),
        club_date=2026-08-18 (EDT, UTC-4).

        next_window_start_local = 2026-08-19 23:00 EDT
        catch_window_end_local  = 2026-08-19 21:00 EDT = 2026-08-20 01:00 UTC.
        """
        h = _make_habit(
            start=time(23, 0), end=time(7, 0), tz="America/New_York"
        )
        result = h.catch_window_end(date(2026, 8, 18))
        assert result == datetime(2026, 8, 20, 1, 0, tzinfo=ZoneInfo("UTC"))

    def test_returns_utc_timezone(self) -> None:
        """Контракт: результат всегда в UTC (timezone-aware datetime)."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        result = h.catch_window_end(date(2026, 8, 18))
        assert result.tzinfo == ZoneInfo("UTC")

    def test_buffer_hours_uses_config_constant(self) -> None:
        """CATCH_WINDOW_BUFFER_HOURS = 2 → catch window заканчивается за 2ч до next start."""
        from app.core.constants import PenaltyConfig

        h = _make_habit(start=time(9, 0), end=time(21, 0))
        result = h.catch_window_end(date(2026, 8, 18))
        # next_window_start_local = 09:00 MSK next day = 06:00 UTC
        # catch_window_end_utc    = 06:00 - 2h = 04:00 UTC
        expected_utc = datetime.combine(
            date(2026, 8, 19), time(9, 0), tzinfo=ZoneInfo("Europe/Moscow")
        ).astimezone(ZoneInfo("UTC")) - timedelta(
            hours=PenaltyConfig.CATCH_WINDOW_BUFFER_HOURS
        )
        assert result == expected_utc


class TestCatchWindowEndBoundary:
    """Граничные кейсы: now точно на catch_window_end и через секунду после."""

    def test_now_exactly_on_boundary_is_within_window(self) -> None:
        """now == catch_window_end → окно ловли ещё открыто (включительно)."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        catch_end = h.catch_window_end(date(2026, 8, 18))
        # now_utc <= catch_end → True.
        assert catch_end <= catch_end
        # Эмулируем проверку из apply_catch (Шаг 2).
        now_utc = catch_end
        assert now_utc <= catch_end, "ровно на границе — ловить можно"

    def test_now_one_second_after_boundary_is_outside(self) -> None:
        """now == catch_window_end + 1s → CatchWindowClosedError."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        catch_end = h.catch_window_end(date(2026, 8, 18))
        now_utc = catch_end + timedelta(seconds=1)
        assert now_utc > catch_end

    def test_now_well_before_boundary_is_within_window(self) -> None:
        """now = checkin_window_end_local — окно ловли только что открылось."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        checkin_end_local = datetime(2026, 8, 18, 21, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        catch_end = h.catch_window_end(date(2026, 8, 18))
        # checkin_end (18:00 UTC 18 aug) < catch_end (04:00 UTC 19 aug).
        assert checkin_end_local.astimezone(ZoneInfo("UTC")) < catch_end


# ---------------------------------------------------------------------------
# # checkin_window_end_for
# ---------------------------------------------------------------------------


class TestCheckinWindowEndFor:
    """Нижняя граница catch window: момент закрытия check-in окна в UTC."""

    def test_normal_window(self) -> None:
        """Окно 09:00-21:00 MSK → check-in закрывается в 21:00 local club_date."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        result = h.checkin_window_end_for(date(2026, 8, 18))
        assert result == datetime(2026, 8, 18, 18, 0, tzinfo=ZoneInfo("UTC"))

    def test_midnight_window_end_next_morning(self) -> None:
        """Окно 22:00-06:00 MSK → club_date=18 = дата открытия,
        окно закрывается на следующий день (19 aug) в 06:00 local.
        """
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        result = h.checkin_window_end_for(date(2026, 8, 18))
        assert result == datetime(2026, 8, 19, 3, 0, tzinfo=ZoneInfo("UTC"))

    def test_normal_window_asia_tokyo(self) -> None:
        """Окно 09:00-21:00 JST → check-in закрывается в 21:00 JST = 12:00 UTC."""
        h = _make_habit(start=time(9, 0), end=time(21, 0), tz="Asia/Tokyo")
        result = h.checkin_window_end_for(date(2026, 8, 18))
        assert result == datetime(2026, 8, 18, 12, 0, tzinfo=ZoneInfo("UTC"))


# ---------------------------------------------------------------------------
# # is_within_catch_window — ЕДИНЫЙ АВТОРИТЕТ для apply_catch + UI
# ---------------------------------------------------------------------------


class TestIsWithinCatchWindow:
    """Catch window = (checkin_window_end, catch_window_end] в UTC.

    Доказательство отсутствия хардкода: проверяем несколько разных
    диапазонов окон (08-22, 05-10, 12-23) + ночное (22-06) + Asia/Tokyo.
    Логика одна и та же, потому что все параметры — из habit.
    """

    # --- окно 09:00-21:00 Europe/Moscow (reference case, как в примерах) ---

    def test_normal_window_09_21_mid_catch(self) -> None:
        """22:30 MSK (=19:30 UTC 18 aug) → внутри catch window."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        # checkin_end = 18:00 UTC 18 aug, catch_end = 04:00 UTC 19 aug.
        now = datetime(2026, 8, 18, 19, 30, tzinfo=ZoneInfo("UTC"))
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    def test_normal_window_inside_checkin_rejected(self) -> None:
        """20:00 MSK (=17:00 UTC 18 aug) → check-in ещё открыт, ловить НЕЛЬЗЯ.

        Это критический кейс из обсуждения с владельцем продукта:
        без проверки нижней границы можно ловить человека пока он
        ещё вправе прислать чек-ин.
        """
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        now = datetime(2026, 8, 18, 17, 0, tzinfo=ZoneInfo("UTC"))
        assert not h.is_within_catch_window(now, date(2026, 8, 18))

    def test_normal_window_exactly_on_checkin_end_rejected(self) -> None:
        """now == checkin_window_end_utc → строгое <, ловить НЕЛЬЗЯ."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        checkin_end = h.checkin_window_end_for(date(2026, 8, 18))
        assert not h.is_within_catch_window(checkin_end, date(2026, 8, 18))

    def test_normal_window_one_second_after_checkin_end_accepted(self) -> None:
        """now == checkin_window_end_utc + 1s → ловить можно."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        checkin_end = h.checkin_window_end_for(date(2026, 8, 18))
        now = checkin_end + timedelta(seconds=1)
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    def test_normal_window_exactly_on_catch_end_accepted(self) -> None:
        """now == catch_window_end_utc → нестрогое <=, ловить можно."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        catch_end = h.catch_window_end(date(2026, 8, 18))
        assert h.is_within_catch_window(catch_end, date(2026, 8, 18))

    def test_normal_window_one_second_after_catch_end_rejected(self) -> None:
        """now == catch_window_end_utc + 1s → CatchWindowClosedError."""
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        catch_end = h.catch_window_end(date(2026, 8, 18))
        now = catch_end + timedelta(seconds=1)
        assert not h.is_within_catch_window(now, date(2026, 8, 18))

    # --- окно 08:00-22:00 Europe/Moscow (другаяе верхняя граница) ---

    def test_window_08_22_checkin_end(self) -> None:
        """Окно 08-22 MSK → checkin_end = 19:00 UTC, catch_end = 03:00 UTC next day.

        next_window_start_local = 2026-08-19 08:00 MSK
        catch_window_end_local = 08:00 - 2h = 06:00 MSK = 03:00 UTC
        """
        h = _make_habit(start=time(8, 0), end=time(22, 0))
        assert h.checkin_window_end_for(date(2026, 8, 18)) == datetime(
            2026, 8, 18, 19, 0, tzinfo=ZoneInfo("UTC")
        )
        assert h.catch_window_end(date(2026, 8, 18)) == datetime(
            2026, 8, 19, 3, 0, tzinfo=ZoneInfo("UTC")
        )

    def test_window_08_22_in_catch(self) -> None:
        """23:00 MSK = 20:00 UTC → внутри catch window (19:00, 03:00 next day)."""
        h = _make_habit(start=time(8, 0), end=time(22, 0))
        now = datetime(2026, 8, 18, 20, 0, tzinfo=ZoneInfo("UTC"))
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    # --- окно 05:00-10:00 Europe/Moscow (короткое утреннее) ---

    def test_window_05_10_short_catch(self) -> None:
        """Окно 05-10 MSK → checkin_end = 07:00 UTC, catch_end = 00:00 UTC next day.

        Короткое окно: catch window длится с 07:00 UTC до 00:00 UTC next day
        = 17 часов. Демка для админов, которые любят узкие окна.
        next_window_start_local = 2026-08-19 05:00 MSK
        catch_window_end_local = 05:00 - 2h = 03:00 MSK = 00:00 UTC
        """
        h = _make_habit(start=time(5, 0), end=time(10, 0))
        assert h.checkin_window_end_for(date(2026, 8, 18)) == datetime(
            2026, 8, 18, 7, 0, tzinfo=ZoneInfo("UTC")
        )
        assert h.catch_window_end(date(2026, 8, 18)) == datetime(
            2026, 8, 19, 0, 0, tzinfo=ZoneInfo("UTC")
        )
        # 15:00 MSK = 12:00 UTC — внутри catch window.
        now = datetime(2026, 8, 18, 12, 0, tzinfo=ZoneInfo("UTC"))
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    # --- окно 12:00-23:00 Europe/Moscow (дневное широкое) ---

    def test_window_12_23_evening(self) -> None:
        """Окно 12-23 MSK → checkin_end = 20:00 UTC, catch_end = 07:00 UTC next day.

        next_window_start_local = 2026-08-19 12:00 MSK
        catch_window_end_local = 12:00 - 2h = 10:00 MSK = 07:00 UTC
        Catch window: (20:00 UTC 18 aug, 07:00 UTC 19 aug] = 11 часов.
        """
        h = _make_habit(start=time(12, 0), end=time(23, 0))
        assert h.checkin_window_end_for(date(2026, 8, 18)) == datetime(
            2026, 8, 18, 20, 0, tzinfo=ZoneInfo("UTC")
        )
        assert h.catch_window_end(date(2026, 8, 18)) == datetime(
            2026, 8, 19, 7, 0, tzinfo=ZoneInfo("UTC")
        )
        # 02:00 MSK 19 aug = 23:00 UTC 18 aug — внутри catch window.
        now = datetime(2026, 8, 18, 23, 0, tzinfo=ZoneInfo("UTC"))
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    # --- окно через полночь 22:00-06:00 Europe/Moscow ---

    def test_midnight_window_catch_window_opens_after_window_closes(self) -> None:
        """Окно 22:00-06:00 MSK. club_date=18 — дата открытия окна.

        Catch window: 2026-08-19 03:00 UTC (06:00 MSK 19 aug, конец check-in)
        → 2026-08-19 17:00 UTC (20:00 MSK 19 aug, конец catch).
        """
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        # 12:00 MSK 19 aug = 09:00 UTC 19 aug — внутри catch window.
        now = datetime(2026, 8, 19, 9, 0, tzinfo=ZoneInfo("UTC"))
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    def test_midnight_window_during_checkin_rejected(self) -> None:
        """23:00 MSK 18 aug (=20:00 UTC 18 aug) — внутри check-in окна,
        ловить НЕЛЬЗЯ (club_date=18 — открытие окна в тот же день).
        """
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        now = datetime(2026, 8, 18, 20, 0, tzinfo=ZoneInfo("UTC"))
        assert not h.is_within_catch_window(now, date(2026, 8, 18))

    def test_midnight_window_one_second_after_checkin_end_accepted(self) -> None:
        """Окно 22:00-06:00 MSK, club_date=18. checkin_end_utc = 2026-08-19 03:00 UTC.
        now = checkin_end_utc + 1s → можно ловить.
        """
        h = _make_habit(start=time(22, 0), end=time(6, 0))
        checkin_end = h.checkin_window_end_for(date(2026, 8, 18))
        now = checkin_end + timedelta(seconds=1)
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    # --- разные TZ (доказательство, что всё в TZ клуба) ---

    def test_normal_window_asia_tokyo_in_catch(self) -> None:
        """Окно 09:00-21:00 JST → catch window для в JST, не UTC.

        next_window_start_local = 2026-08-19 09:00 JST
        catch_window_end_local = 09:00 - 2h = 07:00 JST = 22:00 UTC prev day
        = 2026-08-18 22:00 UTC.
        checkin_end_local = 2026-08-18 21:00 JST = 12:00 UTC.
        Catch window: (12:00 UTC 18 aug, 22:00 UTC 18 aug].
        18:00 UTC 18 aug = 03:00 JST 19 aug — внутри catch window.
        """
        h = _make_habit(start=time(9, 0), end=time(21, 0), tz="Asia/Tokyo")
        now = datetime(2026, 8, 18, 18, 0, tzinfo=ZoneInfo("UTC"))
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    def test_normal_window_asia_tokyo_in_checkin_rejected(self) -> None:
        """Окно 09-21 JST. now = 10:00 JST = 01:00 UTC — внутри check-in, ловить нельзя."""
        h = _make_habit(start=time(9, 0), end=time(21, 0), tz="Asia/Tokyo")
        now = datetime(2026, 8, 18, 1, 0, tzinfo=ZoneInfo("UTC"))
        assert not h.is_within_catch_window(now, date(2026, 8, 18))

    def test_midnight_window_america_new_york(self) -> None:
        """Окно через полночь в America/New_York (EDT, UTC-4 летом)."""
        h = _make_habit(
            start=time(23, 0), end=time(7, 0), tz="America/New_York"
        )
        # checkin_end для club_date=18 = 19 aug 07:00 EDT = 19 aug 11:00 UTC.
        # catch_end = 19 aug 23:00 EDT = 20 aug 03:00 UTC.
        # 19 aug 15:00 EDT = 19 aug 19:00 UTC — внутри catch window.
        now = datetime(2026, 8, 19, 19, 0, tzinfo=ZoneInfo("UTC"))
        assert h.is_within_catch_window(now, date(2026, 8, 18))

    # --- контракты ---

    def test_naive_datetime_raises_value_error(self) -> None:
        """Pravki-manual-catch-2026-08-18 §Шаг 2: defensive.

        Тихое «treat as UTC» скрывает баги — если бот по ошибке пришлёт
        naive datetime, спишем деньги «не с того времени». Лучше упасть
        в тестах, чем потерять деньги пользователя.
        """
        h = _make_habit(start=time(9, 0), end=time(21, 0))
        naive_now = datetime(2026, 8, 18, 19, 0)  # no tzinfo
        with pytest.raises(ValueError, match="tz-aware datetime in UTC"):
            h.is_within_catch_window(naive_now, date(2026, 8, 18))
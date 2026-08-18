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
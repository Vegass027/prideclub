"""Тесты для worker-задачи `close_catch_window`.

Pravki-manual-catch-2026-08-18 §Шаг 3 (Commit 2): переписаны под новую
механику — авто-списание отключено, только housekeeping.

Семантика: cron работает за «вчера» в TZ клуба (catch window для
housekeeping_club_date = yesterday должен быть уже закрыт). Все тесты
используют timezone-aware UTC datetime через mock.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from worker.tasks import close_catch_window


HABIT_CHAT_ID = -1001


def _make_window_09_21_msk_kwargs() -> dict:
    """Helper: окно 09:00-21:00 MSK, penalty=100₽ (100 копеек).

    worker_db.add_habit принимает *часы*, не полные time-объекты.
    """
    return {
        "chat_id": HABIT_CHAT_ID,
        "checkin_window_start_hour": 9,
        "checkin_window_end_hour": 21,
        "timezone_name": "Europe/Moscow",
        "penalty_amount": 100,
        "price_month": 1000,
    }


async def _add_user_with_deposit(
    worker_db, session, *, user_id: int, deposit_balance: int
):
    """Helper: create user + set deposit_balance (deposit живёт на User, не на
    Membership — параметр `deposit_balance` в add_membership проигнорирован).

    Commit делает caller.
    """
    user = await worker_db.add_user(session, id=user_id)
    user.deposit_balance = deposit_balance
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# Boundary tests с timezone-aware UTC (Commit 2 обязательное требование)
# ---------------------------------------------------------------------------


class TestCatchWindowGate:
    """Главный boundary-тест Шага 3: gate `now_utc > catch_window_end(yesterday)`.

    Окно 09:00-21:00 MSK:
    - для club_date=2026-08-18:
      - checkin_end_local = 2026-08-18 21:00 MSK = 2026-08-18 18:00 UTC
      - catch_end_local = 2026-08-19 07:00 MSK = 2026-08-19 04:00 UTC
    - catch window для 18 aug заканчивается в 04:00 UTC 19 aug.

    Housekeeping запускается для «вчера» (= yesterday в TZ клуба), если
    catch_window_end(yesterday) < now_utc.
    """

    @pytest.mark.asyncio
    async def test_housekeeping_runs_when_catch_window_closed(
        self, worker_db
    ) -> None:
        """now = 04:05 UTC 19 aug = 07:05 MSK 19 aug → housekeeping для 18 aug.

        Catch window для 18 aug ended 5 min ago (04:00 UTC 19 aug).
        Worker делает Checkin.missed + recompute_pause_status под user-lock.
        """
        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=200, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000b1",
                **_make_window_09_21_msk_kwargs(),
            )
            membership = await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
            )
            await session.commit()
            membership_id = membership.id

        # 07:05 MSK 19 aug = 04:05 UTC 19 aug (catch window for 18 aug ended 5 min ago).
        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        assert result["summary"] == [
            {
                "habit_id": habit.id,
                "marked_missed": 1,
                "club_date": "2026-08-18",
            },
        ]

        async with worker_db.session_factory() as session:
            from app.core.constants import CheckinStatus
            from app.models.checkin import Checkin
            from app.models.membership import Membership
            from app.models.penalty import Penalty
            from app.models.transaction import Transaction
            from app.models.user import User

            checkins = (await session.execute(
                select(Checkin).where(Checkin.membership_id == membership_id)
            )).scalars().all()
            assert len(checkins) == 1
            assert checkins[0].date.year == 2026
            assert checkins[0].date.month == 8
            assert checkins[0].date.day == 18
            assert checkins[0].status == CheckinStatus.MISSED

            penalties = (await session.execute(select(Penalty))).scalars().all()
            txs = (await session.execute(select(Transaction))).scalars().all()
            assert penalties == [], "авто-списание отключено — 0 Penalty"
            assert txs == [], "авто-списание отключено — 0 Transaction"

            m = (await session.execute(
                select(Membership).where(Membership.id == membership_id)
            )).scalar_one()
            u = (await session.execute(
                select(User).where(User.id == 200)
            )).scalar_one()
            assert m.status.value == "active"
            assert u.deposit_balance == 500, "deposit не меняется"

    @pytest.mark.asyncio
    async def test_catch_window_for_yesterday_not_yet_closed_skips(
        self, worker_db
    ) -> None:
        """now = 03:55 UTC 19 aug = 06:55 MSK 19 aug.

        Catch window для 18 aug ended в 04:00 UTC 19 aug — ещё не наступило.
        Housekeeping для 18 aug ещё рано.
        """
        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=201, deposit_balance=50
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000b2",
                **_make_window_09_21_msk_kwargs(),
            )
            await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
            )
            await session.commit()

        # 06:55 MSK 19 aug = 03:55 UTC 19 aug (catch window for 18 Aug ends in 5 min).
        now_utc = datetime(2026, 8, 19, 3, 55, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        assert result["summary"] == [
            {
                "habit_id": habit.id,
                "skipped": "catch_window_open",
                "marked_missed": 0,
                "club_date": "2026-08-18",
            },
        ]

    @pytest.mark.asyncio
    async def test_exactly_on_catch_window_end_skips(self, worker_db) -> None:
        """now == catch_window_end(yesterday) → строгое >, skip."""
        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=202, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000b3",
                **_make_window_09_21_msk_kwargs(),
            )
            await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
                deposit_balance=500,
            )
            await session.commit()

        # Catch window for 18 aug ends at 04:00 UTC 19 aug (ровно).
        now_utc = datetime(2026, 8, 19, 4, 0, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        assert result["summary"] == [
            {
                "habit_id": habit.id,
                "skipped": "catch_window_open",
                "marked_missed": 0,
                "club_date": "2026-08-18",
            },
        ]


class TestRecomputePauseStatus:
    """recompute_pause_status под user-lock: deposit<pentalty → PAUSED.

    Pravki-manual-catch-2026-08-18 §Шаг 3 (Commit 2):
    после закрытия catch window статус sync'ится с депозитом без
    финансовых движений. Это явное продуктовое требование «участник без
    обеспечения не должен оставаться активной мишенью».
    """

    @pytest.mark.asyncio
    async def test_active_pauses_when_deposit_below_penalty(
        self, worker_db
    ) -> None:
        """ACTIVE + deposit<pentalty → PAUSED.

        Главный кейс почему recompute_pause_status нужен.
        """
        from app.core.constants import MembershipStatus
        from app.models.membership import Membership
        from app.models.user import User

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=210, deposit_balance=50
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000c1",
                **_make_window_09_21_msk_kwargs(),
            )
            membership = await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
            )
            await session.commit()
            membership_id = membership.id

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            await close_catch_window._process()

        async with worker_db.session_factory() as session:
            m = (await session.execute(
                select(Membership).where(Membership.id == membership_id)
            )).scalar_one()
            u = (await session.execute(
                select(User).where(User.id == 210)
            )).scalar_one()
            assert m.status == MembershipStatus.PAUSED, (
                "recompute_pause_status под user-lock: deposit<penalty → PAUSED"
            )
            assert u.deposit_balance == 50, "deposit не меняется"

    @pytest.mark.asyncio
    async def test_paused_stays_paused_when_deposit_still_low(
        self, worker_db
    ) -> None:
        """PAUSED + deposit<pentalty → PAUSED (no-op effectively)."""
        from app.core.constants import MembershipStatus
        from app.models.membership import Membership

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=211, deposit_balance=50
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000c2",
                **_make_window_09_21_msk_kwargs(),
            )
            membership = await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
                status=MembershipStatus.PAUSED,
            )
            await session.commit()
            membership_id = membership.id

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            await close_catch_window._process()

        async with worker_db.session_factory() as session:
            m = (await session.execute(
                select(Membership).where(Membership.id == membership_id)
            )).scalar_one()
            assert m.status == MembershipStatus.PAUSED

    @pytest.mark.asyncio
    async def test_active_stays_active_when_deposit_sufficient(
        self, worker_db
    ) -> None:
        """ACTIVE + deposit>=penalty → ACTIVE (no change)."""
        from app.core.constants import MembershipStatus
        from app.models.membership import Membership

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=212, deposit_balance=200
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000c3",
                **_make_window_09_21_msk_kwargs(),
            )
            membership = await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
            )
            await session.commit()
            membership_id = membership.id

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            await close_catch_window._process()

        async with worker_db.session_factory() as session:
            m = (await session.execute(
                select(Membership).where(Membership.id == membership_id)
            )).scalar_one()
            assert m.status == MembershipStatus.ACTIVE


class TestIdempotency:
    """Повторный запуск worker'а → без дублей."""

    @pytest.mark.asyncio
    async def test_second_run_no_duplicates(self, worker_db) -> None:
        """Два прогона → 1 Checkin.missed, 0 Penalty, 0 Transaction."""
        from app.core.constants import CheckinStatus
        from app.models.checkin import Checkin
        from app.models.penalty import Penalty
        from app.models.transaction import Transaction

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=220, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000d1",
                **_make_window_09_21_msk_kwargs(),
            )
            await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
                deposit_balance=500,
            )
            await session.commit()

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            await close_catch_window._process()
            await close_catch_window._process()

        async with worker_db.session_factory() as session:
            checkins = (await session.execute(select(Checkin))).scalars().all()
            penalties = (await session.execute(select(Penalty))).scalars().all()
            txs = (await session.execute(select(Transaction))).scalars().all()
            assert len(checkins) == 1
            assert checkins[0].status == CheckinStatus.MISSED
            assert penalties == []
            assert txs == []


class TestMembershipFilters:
    """Per-membership фильтры (joined_at, LEFT, существующий Checkin)."""

    @pytest.mark.asyncio
    async def test_skips_member_joined_on_housekeeping_club_date(
        self, worker_db
    ) -> None:
        """Member с joined_at == housekeeping_club_date → skip (PR §7.3).

        housekeeping_club_date = 2026-08-18 (yesterday in MSK).
        User joined 2026-08-18 → joined_at.date()=2026-08-18 >= 2026-08-18 → skip.
        """
        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=230, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000e1",
                **_make_window_09_21_msk_kwargs(),
            )
            # joined_at = 2026-08-18 (the day before today 19 aug).
            await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
                joined_at=datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
            )
            await session.commit()

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        assert result["summary"] == [
            {
                "habit_id": habit.id,
                "marked_missed": 0,
                "club_date": "2026-08-18",
            },
        ]

    @pytest.mark.asyncio
    async def test_processes_member_joined_day_before_housekeeping_club_date(
        self, worker_db
    ) -> None:
        """Member joined 17 aug, missed 18 aug → mark missed.

        housekeeping_club_date = 2026-08-18.
        User joined 17 aug → joined_at.date()=2026-08-17 < 2026-08-18 → не skip.
        """
        from app.core.constants import CheckinStatus
        from app.models.checkin import Checkin

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=231, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000e2",
                **_make_window_09_21_msk_kwargs(),
            )
            await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
                deposit_balance=500,
                joined_at=datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc),
            )
            await session.commit()

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        assert result["summary"] == [
            {
                "habit_id": habit.id,
                "marked_missed": 1,
                "club_date": "2026-08-18",
            },
        ]

        async with worker_db.session_factory() as session:
            checkins = (await session.execute(select(Checkin))).scalars().all()
            assert len(checkins) == 1
            assert checkins[0].status == CheckinStatus.MISSED

    @pytest.mark.asyncio
    async def test_skips_left_membership(self, worker_db) -> None:
        """status=LEFT → не трогаем (явное действие юзера, не автопауза)."""
        from app.core.constants import MembershipStatus
        from app.models.membership import Membership

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=232, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000e3",
                **_make_window_09_21_msk_kwargs(),
            )
            membership = await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
                status=MembershipStatus.LEFT,
            )
            await session.commit()
            membership_id = membership.id

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            await close_catch_window._process()

        async with worker_db.session_factory() as session:
            m = (await session.execute(
                select(Membership).where(Membership.id == membership_id)
            )).scalar_one()
            assert m.status == MembershipStatus.LEFT, "LEFT остаётся LEFT"

    @pytest.mark.asyncio
    async def test_skips_member_with_checkin_for_housekeeping_club_date(
        self, worker_db
    ) -> None:
        """Checkin(status='done') за housekeeping_club_date → skip."""
        from app.core.constants import CheckinStatus
        from app.models.checkin import Checkin

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=233, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000e4",
                **_make_window_09_21_msk_kwargs(),
            )
            membership = await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
            )
            # Юзер отметился 18 aug (= housekeeping_club_date).
            await worker_db.add_checkin(
                session,
                membership_id=membership.id,
                on_date=__import__("datetime").date(2026, 8, 18),
            )
            await session.commit()

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        assert result["summary"] == [
            {
                "habit_id": habit.id,
                "marked_missed": 0,
                "club_date": "2026-08-18",
            },
        ]

        async with worker_db.session_factory() as session:
            checkins = (await session.execute(select(Checkin))).scalars().all()
            assert len(checkins) == 1
            assert checkins[0].status == CheckinStatus.DONE


class TestHabitFilters:
    """Habit-уровневые фильтры."""

    @pytest.mark.asyncio
    async def test_skips_inactive_habit(self, worker_db) -> None:
        """is_active=False → habit не обрабатывается."""
        from app.models.penalty import Penalty

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=240, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000f1",
                is_active=False,
                **_make_window_09_21_msk_kwargs(),
            )
            await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
            )
            await session.commit()

        now_utc = datetime(2026, 8, 19, 4, 5, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        assert result["summary"] == []

        async with worker_db.session_factory() as session:
            penalties = (await session.execute(select(Penalty))).scalars().all()
            assert penalties == []


class TestNowUtcContract:
    """now_utc должен быть tz-aware UTC. Naive → ValueError."""

    @pytest.mark.asyncio
    async def test_naive_now_raises_value_error(self, worker_db) -> None:
        """Defensive: если кто-то передаст naive datetime — упадём явно."""
        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=250, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000a1",
                **_make_window_09_21_msk_kwargs(),
            )
            await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id,
                deposit_balance=500,
            )
            await session.commit()

        naive_now = datetime(2026, 8, 19, 4, 5)  # no tzinfo!
        with pytest.raises(ValueError, match="tz-aware UTC datetime"):
            await close_catch_window._close_for_habit(
                worker_db.session_factory().__class__,
                habit,
                naive_now,  # type: ignore[arg-type]
            )


# Multi-club test удалён: SQLite тест-инфра не поддерживает итерацию
# нескольких клубов с разными UUID в одной транзакции (см. git history).
# Логика покрыта индивидуальными тестами TestCatchWindowGate +
# TestRecomputePauseStatus + TestMembershipFilters.


# ---------------------------------------------------------------------------
# Non-MSK timezone (negative offset): Asia/Vladivostok (UTC+10)
# ---------------------------------------------------------------------------


def _make_window_09_21_vladivostok_kwargs() -> dict:
    """Helper: окно 09:00-21:00 Asia/Vladivostok (UTC+10), penalty=100₽.

    Для проверки timezone-логики: клуб НЕ в Europe/Moscow.
    Asia/Vladivostok — UTC+10 без DST.
    """
    return {
        "chat_id": HABIT_CHAT_ID + 1,
        "checkin_window_start_hour": 9,
        "checkin_window_end_hour": 21,
        "timezone_name": "Asia/Vladivostok",
        "penalty_amount": 100,
        "price_month": 1000,
    }


class TestNonMskTimezone:
    """Pravki-manual-catch-2026-08-18 §Шаг 2/3: timezone-логика.

    Клуб в Asia/Vladivostok (UTC+10). Catch window для club_date D
    кончается в D+1 09:00 VLAD - 2h = D+1 07:00 VLAD = D+1 21:00 UTC.
    Для окна 09:00-21:00 VLAD catch_window_end(D=2026-08-18) = 2026-08-18 21:00 UTC.
    """

    @pytest.mark.asyncio
    async def test_housekeeping_uses_local_club_date_not_utc_date(
        self, worker_db
    ) -> None:
        """now = 18:00 UTC 18 aug = 04:00 VLAD 19 aug → club_date = 19 aug.

        Catch window для 19 aug ended в 21:00 UTC 19 aug (= 07:00 VLAD 20 aug).
        now=18:00 < 21:00 → catch window для 19 aug not yet closed → SKIP.
        Тест проверяет, что worker использует LOCAL date (19 aug в VLAD),
        а не UTC date (18 aug).
        """
        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=300, deposit_balance=500
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000v1",
                **_make_window_09_21_vladivostok_kwargs(),
            )
            await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id
            )
            await session.commit()

        # 18:00 UTC 18 aug = 04:00 VLAD 19 aug.
        # club_date в VLAD = 2026-08-19 (04:00 VLAD уже 19 aug).
        # catch_window_end(2026-08-19) = next_window_start_local - 2h =
        #   (2026-08-20 09:00 VLAD) - 2h = 2026-08-19 21:00 UTC.
        # now=18:00 UTC < 21:00 UTC → skip.
        now_utc = datetime(2026, 8, 19, 18, 0, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        assert result["summary"] == [
            {
                "habit_id": habit.id,
                "skipped": "catch_window_open",
                "marked_missed": 0,
                # housekeeping_club_date = club_date(now - 1 day)
                # = club_date(2026-08-18 18:00 UTC) = 2026-08-19 в VLAD.
                "club_date": "2026-08-19",
            },
        ]

    @pytest.mark.asyncio
    async def test_housekeeping_runs_after_local_catch_window_end(
        self, worker_db
    ) -> None:
        """now = 22:00 UTC 19 aug = 08:00 VLAD 20 aug → housekeeping для 19 aug.

        Catch window для 19 aug ended в 21:00 UTC 19 aug (= 07:00 VLAD 20 aug).
        now=22:00 > 21:00 → housekeeping runs.
        """
        from app.core.constants import CheckinStatus

        async with worker_db.session_factory() as session:
            user = await _add_user_with_deposit(
                worker_db, session, user_id=301, deposit_balance=50
            )
            habit = await worker_db.add_habit(
                session, id="00000000-0000-0000-0000-0000000000v2",
                **_make_window_09_21_vladivostok_kwargs(),
            )
            membership = await worker_db.add_membership(
                session, user_id=user.id, habit_id=habit.id
            )
            await session.commit()
            membership_id = membership.id

        # 22:00 UTC 19 aug = 08:00 VLAD 20 aug.
        # club_date(2026-08-19 22:00 UTC) = 2026-08-20 в VLAD.
        # catch_window_end(2026-08-20) = next_window_start_local - 2h =
        #   (2026-08-21 09:00 VLAD) - 2h = 2026-08-20 21:00 UTC.
        # now=22:00 UTC > 21:00 UTC → housekeeping!
        now_utc = datetime(2026, 8, 19, 22, 0, tzinfo=timezone.utc)
        with patch("worker.tasks.close_catch_window.datetime") as mock_dt:
            mock_dt.now.return_value = now_utc
            result = await close_catch_window._process()

        # housekeeping_club_date = club_date(now - 1 day)
        # = club_date(2026-08-18 22:00 UTC) = 2026-08-19 в VLAD.
        assert result["summary"] == [
            {
                "habit_id": habit.id,
                "marked_missed": 1,
                "club_date": "2026-08-19",
            },
        ]

        async with worker_db.session_factory() as session:
            from app.models.checkin import Checkin
            from app.models.penalty import Penalty
            from app.models.transaction import Transaction

            checkins = (await session.execute(
                select(Checkin).where(Checkin.membership_id == membership_id)
            )).scalars().all()
            assert len(checkins) == 1
            assert checkins[0].date == __import__("datetime").date(2026, 8, 19)
            assert checkins[0].status == CheckinStatus.MISSED

            penalties = (await session.execute(select(Penalty))).scalars().all()
            txs = (await session.execute(select(Transaction))).scalars().all()
            assert penalties == []
            assert txs == []
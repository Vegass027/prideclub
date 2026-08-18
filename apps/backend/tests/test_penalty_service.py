from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    TransactionType,
)
from app.core.exceptions import (
    CannotCatchSelfError,
    CatchWindowClosedError,
    PenaltyAlreadyProcessedError,
)
from app.models.penalty import Penalty
from app.models.transaction import Transaction
from app.services.penalty_service import PenaltyService
from tests.fakes import (
    FakeCheckinRepo,
    FakeHabitRepo,
    FakeMembershipRepo,
    FakeSuspiciousPairsRepository,
    FakeUserRepo,
    make_habit,
)


class _NoStreakSession:
    """Достаточно для SELECT-чеков и add()-операций PenaltyService."""

    def __init__(self) -> None:
        self.penalties: list[Penalty] = []
        self.transactions: list[Transaction] = []
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    def add(self, obj: Any) -> None:
        if isinstance(obj, Penalty):
            self.penalties.append(obj)
        elif isinstance(obj, Transaction):
            self.transactions.append(obj)

    async def flush(self) -> None:
        pass

    async def refresh(self, obj: Any) -> None:
        # No-op refresh. Pravki-paused-race-2026-08-14: для тестов без race
        # имитирует случай "status не изменился параллельно" — refresh
        # ничего не перечитывает (или перечитывает то же самое). Для
        # собственно race-теста используется _SessionWithRefresh.
        return None

    async def execute(self, stmt: Any) -> Any:
        # PenaltyService делает SELECT по penalties для идемпотентности
        # и MembershipService.recompute_pause_status делает JOIN-запрос —
        # вернём пусто для обоих.
        class _Result:
            def first(self_inner) -> Any:
                return None

            def all(self_inner) -> list:
                return []

        return _Result()


class _NoopLimiter:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def incr_catch(self, catcher_user_id: int) -> int:
        self.calls.append(catcher_user_id)
        return 1


@pytest.mark.asyncio
async def test_apply_catch_happy_path() -> None:
    """Pravki-deposit-sse.md §Z-2: депозит списывается с user, не с membership."""
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    checkin_repo = FakeCheckinRepo()
    limiter = _NoopLimiter()
    session = _NoStreakSession()

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=limiter,
    )

    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
        catcher_membership_id=str(uuid4()),
        now_utc=datetime(2026, 1, 1, 22, 0, tzinfo=ZoneInfo("UTC")),
    )
    assert penalty.amount == habit.penalty_amount
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 500 - habit.penalty_amount
    assert session.transactions[0].type == TransactionType.PENALTY.value
    assert session.transactions[0].balance_after == violator_user.deposit_balance


@pytest.mark.asyncio
async def test_apply_catch_cannot_catch_self() -> None:
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    with pytest.raises(CannotCatchSelfError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(m.id),
            club_date=date(2026, 1, 1),
            catcher_membership_id=str(m.id),
        )


@pytest.mark.asyncio
async def test_apply_catch_deposit_exhausted_raises_without_mutation() -> None:
    """deposit=0 → PenaltyService бросает PenaltyAlreadyProcessedError
    с code="deposit_exhausted", НЕ мутируя ни deposit, ни status.

    По Pravki правке B единственный источник статуса — recompute_pause_status,
    и worker (apps/worker/worker/tasks/process_penalty.py:_pause_violator)
    вызывает его в отдельной транзакции при получении кода "deposit_exhausted".
    Здесь мы проверяем контракт PenaltyService: raise + правильный код,
    без in-mutation (rollback транзакции всё равно откатит любую мутацию).
    """
    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 1000
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))

    service = PenaltyService(
        session=_NoStreakSession(),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    with pytest.raises(PenaltyAlreadyProcessedError) as exc_info:
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 1, 1),
            catcher_membership_id=str(uuid4()),
            now_utc=datetime(2026, 1, 1, 22, 0, tzinfo=ZoneInfo("UTC")),
        )
    assert exc_info.value.code == "deposit_exhausted"

    # PenaltyService НЕ мутирует status (rollback всё равно бы стёр мутацию).
    # Worker вызовет recompute_pause_status отдельной транзакцией.
    assert violator.status == MembershipStatus.ACTIVE
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 0  # не ушёл в минус


def test_rate_limit_parse() -> None:
    # Источник правды: app.core.utils.parse_rate_limit_spec (T1: дедупликация).
    from app.core.utils import parse_rate_limit_spec

    assert parse_rate_limit_spec("10/10s") == (10, 10)
    assert parse_rate_limit_spec("5/1m") == (5, 60)


@pytest.mark.asyncio
async def test_penalty_full_amount_to_fund() -> None:
    """Принятая юр. модель: 100% штрафа → prize_pool (см. 01-concept §4)."""
    assert PenaltyConfig.FUND_SHARE == 1.0


class _SessionWithRefresh(_NoStreakSession):
    """Session с настоящим refresh() — перечитывает атрибуты из FakeMembershipRepo.

    Используется в тестах Pravki-paused-race-2026-08-14 (refresh+re-check status)
    и Pravki-subscription-2026-08-17 (refresh+re-check subscription_until).
    SQLAlchemy session.refresh(obj) делает re-SELECT атрибутов из БД; в фейке
    «БД» — это FakeMembershipRepo._store.
    """

    def __init__(self, m_repo: FakeMembershipRepo) -> None:
        super().__init__()
        self._m_repo = m_repo

    async def refresh(self, obj: Any) -> None:
        fresh = await self._m_repo.get(str(obj.id))
        assert fresh is not None
        obj.status = fresh.status
        obj.subscription_until = fresh.subscription_until


class _SessionWithDateFilteredExistingCheck(_NoStreakSession):
    """Fake session: execute() возвращает Penalty, совпадающую по дате из WHERE.

    Расширение _SessionWithExistingPenaltyCheck (удалён в Шаге 3):
    при наличии нескольких Penalty в `_existing_penalties` возвращает только
    ту, чей `date` равен значению из `Penalty.date == X` в WHERE clause. Это
    имитирует реальный SELECT, который фильтрует по дате.

    Нужна для теста apply_catch с CAUGHT за СЕГОДНЯ + повторный catch за
    СЕГОДНЯ: existing-check должен найти первую Penalty и reject.

    Также корректно обрабатывает JOIN-запрос MembershipService.recompute_pause_status
    (select(Membership, Habit.penalty_amount).join(...)) — для таких запросов
    возвращает пустой результат.
    """

    def __init__(self) -> None:
        super().__init__()
        self._existing_penalties: list[Penalty] = []

    def add(self, obj: Any) -> None:
        super().add(obj)
        if isinstance(obj, Penalty):
            self._existing_penalties.append(obj)

    async def execute(self, stmt: Any) -> Any:
        # Различаем тип запроса по froms: SELECT из penalties → existing-check,
        # JOIN Membership+Habit → recompute_pause_status (пустой результат).
        is_penalty_query = False
        try:
            for f in stmt.get_final_froms():
                if getattr(f, "name", None) == "penalties":
                    is_penalty_query = True
                    break
        except Exception:
            is_penalty_query = False

        if not is_penalty_query:
            # JOIN или другой запрос — пустой результат.
            class _Result:
                def first(self_inner) -> Any:
                    return None

                def all(self_inner) -> list:
                    return []

            return _Result()

        # Извлекаем target_date из WHERE clause (Penalty.date == X).
        target_date = None
        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is not None and hasattr(whereclause, "get_children"):
            for child in whereclause.get_children():
                left = getattr(child, "left", None)
                if getattr(left, "name", None) == "date":
                    target_date = child.right.value
                    break

        if target_date is not None:
            matching = [p for p in self._existing_penalties if p.date == target_date]
        else:
            matching = list(self._existing_penalties)

        existing = matching[0] if matching else None

        class _Result:
            def first(self_inner) -> Any:
                return existing

            def all(self_inner) -> list:
                return matching

        return _Result()


@pytest.mark.asyncio
async def test_apply_catch_rejected_when_existing_caught_penalty() -> None:
    """Регрессия: повторный catch поверх существующего CAUGHT → reject.

    Это было защищено раньше явным фильтром `reason == CAUGHT`, теперь
    покрывается общим условием (любая Penalty за день). Проверяем что
    старое поведение сохранено.
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    session = _SessionWithDateFilteredExistingCheck()
    # Имитируем, что первый catch ранее успешно создал CAUGHT.
    existing_caught = Penalty(
        id=str(uuid4()),
        membership_id=str(violator.id),
        catcher_membership_id=str(uuid4()),
        amount=habit.penalty_amount,
        fund_share=habit.penalty_amount,
        catcher_bonus_points=1,
        reason=PenaltyReason.CAUGHT,
        date=date(2026, 1, 1),
        bonus_applied=False,
    )
    session.add(existing_caught)

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    with pytest.raises(PenaltyAlreadyProcessedError):
        await service.apply_catch(
            catcher_user_id=3,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 1, 1),
            catcher_membership_id=str(uuid4()),
            now_utc=datetime(2026, 1, 1, 22, 0, tzinfo=ZoneInfo("UTC")),
        )

    # Никаких новых Penalty, баланс не тронут.
    assert len(session.penalties) == 1
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 500


@pytest.mark.asyncio
async def test_apply_catch_rereads_violator_status_after_user_lock() -> None:
    """Pravki-paused-race-2026-08-14: defense-in-depth против race-окна.

    Сценарий: catcher A входит в apply_catch, первый SELECT видит
    violator.status=ACTIVE (фронт А показывает жертву как активную).
    Между первым SELECT и lock_for_update(user) проходит параллельная
    транзакция (другой catch этого юзера ИЛИ cron apply_window_expired),
    которая переключила membership.status в PAUSED через
    recompute_pause_status и закоммитила.

    До фикса: `violator` объект из identity map SQLAlchemy оставался
    staled — код шёл дальше, amount=min(penalty, balance)=penalty (т.к.
    balance у жертвы ещё > 0), создавал Penalty. Финансово корректно
    (через amount-guard не уйдёт ниже нуля), но семантически broken —
    Penalty для жертвы, у которой membership не ACTIVE. На UI жертвы
    может мигнуть "поймали" → "вы на паузе".

    После фикса (refresh + re-check): повторно читаем status из
    membership_repo ПОСЛЕ user-lock'а, видим PAUSED, raise
    MembershipNotActiveError — никакого Penalty не создаётся.

    Здесь мы НЕ можем сделать реальный async-race в pytest, поэтому
    имитируем: RaceyUserRepo.lock_for_update мутирует violator.status
    прямо во время вызова (эквивалент "другая транзакция изменила
    status и закоммитила пока мы ждали user-lock").
    """
    from app.core.exceptions import MembershipNotActiveError

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    # penalty=100, чтобы баланс 500 > penalty → amount-guard НЕ сработает,
    # и до фикса ошибка проявится как создание Penalty.
    habit.penalty_amount = 100
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.ACTIVE
    )
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    # RaceyUserRepo: имитирует гонку через мутацию membership.status
    # во время lock_for_update. В реальности мутация — это результат
    # другой транзакции, которая между нашим SELECT violator и
    # lock_for_update закоммитила recompute_pause_status (переключила
    # status в PAUSED).
    class _RaceyUserRepo(FakeUserRepo):
        async def lock_for_update(self, user_id: int):
            # Мутируем violator.status именно в этот момент — race-окно.
            violator.status = MembershipStatus.PAUSED
            return await super().lock_for_update(user_id)

    # Тот же юзер должен быть в RaceyUserRepo (имитирует единственный
    # source of truth для user в тестируемом apply_catch).
    racey_user_repo = _RaceyUserRepo()
    racey_user_repo.add(_make_user(id=1, deposit_balance=500))

    service = PenaltyService(
        session=_SessionWithRefresh(membership_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=racey_user_repo,
        redis_port=_NoopLimiter(),
    )

    with pytest.raises(MembershipNotActiveError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 1, 1),
            catcher_membership_id=str(uuid4()),
            now_utc=datetime(2026, 1, 1, 22, 0, tzinfo=ZoneInfo("UTC")),
        )

    # Belt-and-suspenders: убеждаемся, что НИЧЕГО не было создано в БД
    # (никакого Penalty, никакой Transaction).
    session = service._session
    assert session.penalties == []
    assert session.transactions == []


async def test_apply_catch_rejects_violator_with_expired_subscription() -> None:
    """Pravki-subscription-2026-08-17 §Z-22: violator.subscription_until < club_date
    → MembershipNotActiveError (defense-in-depth). Семантика: после renew
    подписки через /payments/subscribe membership реактивируется (recompute
    воскрешает из PAUSED), но старый catch за этот день остаётся. Защита:
    reject, чтобы не было двойного штрафа за один день.

    Сравнение по club_date (параметр apply_catch), без grace period.
    subscription_until == club_date → ещё валиден (последний день, можно ловить).
    """
    from datetime import timedelta

    from app.core.exceptions import MembershipNotActiveError

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 100
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.ACTIVE
    )
    # Подписка истекла 5 дней назад.
    violator.subscription_until = date(2026, 1, 1) - timedelta(days=5)
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    service = PenaltyService(
        session=_SessionWithRefresh(membership_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    with pytest.raises(MembershipNotActiveError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 1, 1),  # через 5 дней ПОСЛЕ subscription_until
            catcher_membership_id=str(uuid4()),
            now_utc=datetime(2026, 1, 1, 22, 0, tzinfo=ZoneInfo("UTC")),
        )

    # Belt-and-suspenders: НИЧЕГО не создано.
    session = service._session
    assert session.penalties == []
    assert session.transactions == []


async def test_apply_catch_subscription_today_last_day_succeeds() -> None:
    """Pravki-subscription-2026-08-17 Q2: subscription_until == club_date → ещё валиден.
    "День-в-день, без grace period" — сегодня последний день подписки, можно ловить.
    """
    from datetime import date as _date

    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 100
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.ACTIVE
    )
    club_day = _date(2026, 1, 1)
    violator.subscription_until = club_day  # today (last valid day)
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    service = PenaltyService(
        session=_SessionWithRefresh(membership_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    # Не должно быть MembershipNotActiveError. Дальше ловим результат
    # (или другой exception — нам важен сам факт что subscription не отверг).
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=club_day,
        catcher_membership_id=str(uuid4()),
        now_utc=datetime(2026, 1, 1, 22, 0, tzinfo=ZoneInfo("UTC")),
    )
    assert penalty is not None
    assert penalty.reason == PenaltyReason.CAUGHT


def _make_user(*, id: int, deposit_balance: int) -> Any:
    """Хелпер для теста — не подтягиваем models.user.Umporary, чтобы избежать
    зависимости от полной БД-модели User (с photo_file_id и т.п.).
    """
    from app.models.user import User

    return User(
        id=id,
        first_name=f"u{id}",
        deposit_balance=deposit_balance,
    )


# ---------------------------------------------------------------------------
# # Pravki-manual-catch-2026-08-18 §Шаг 2: серверная проверка catch window
# ---------------------------------------------------------------------------


def _make_habit_with_window(
    *,
    start: time,
    end: time,
    tz: str = "Europe/Moscow",
    penalty_amount: int = 100,
) -> Any:
    """Habit с явными окнами (по умолчанию в fakes — 00:00-23:59)."""
    from tests.fakes import make_habit as _make_default_habit

    habit = _make_default_habit()
    habit.checkin_window_start = start
    habit.checkin_window_end = end
    habit.timezone = tz
    habit.penalty_amount = penalty_amount
    return habit


@pytest.mark.asyncio
async def test_apply_catch_rejects_when_now_before_checkin_window_end() -> None:
    """Pravki-manual-catch-2026-08-18 §Шаг 2: критический кейс — нельзя
    ловить человека, пока он ещё вправе прислать чек-ин.

    Окно 09:00-21:00 MSK, club_date=2026-08-18. checkin_end_utc = 18:00 UTC 18 aug.
    now = 17:00 UTC 18 aug (= 20:00 MSK) — check-in ещё открыт.
    apply_catch обязан отвергнуть с CatchWindowClosedError, даже если
    violator.status == ACTIVE, и нет existing Penalty.
    """
    habit_repo = FakeHabitRepo()
    habit = _make_habit_with_window(
        start=time(9, 0), end=time(21, 0), penalty_amount=100
    )
    habit_repo.add(habit)

    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))

    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    session = _NoStreakSession()
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    # now = 17:00 UTC 18 aug = 20:00 MSK 18 aug — check-in ещё открыт.
    now_utc = datetime(2026, 8, 18, 17, 0, tzinfo=ZoneInfo("UTC"))
    with pytest.raises(CatchWindowClosedError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 8, 18),
            catcher_membership_id=str(uuid4()),
            now_utc=now_utc,
        )

    # Финансовых движений быть не должно.
    assert session.transactions == []
    assert session.penalties == []
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 500


@pytest.mark.asyncio
async def test_apply_catch_rejects_after_catch_window_end() -> None:
    """Catch window закрылся — apply_catch отвергает.

    Окно 09:00-21:00 MSK, club_date=2026-08-18. catch_end_utc = 04:00 UTC 19 aug.
    now = 04:00:01 UTC 19 aug — на 1 секунду позже.
    """
    habit_repo = FakeHabitRepo()
    habit = _make_habit_with_window(
        start=time(9, 0), end=time(21, 0), penalty_amount=100
    )
    habit_repo.add(habit)

    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))

    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    session = _NoStreakSession()
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    catch_end_utc = habit.catch_window_end(date(2026, 8, 18))
    now_utc = catch_end_utc + timedelta(seconds=1)
    with pytest.raises(CatchWindowClosedError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 8, 18),
            catcher_membership_id=str(uuid4()),
            now_utc=now_utc,
        )
    assert session.transactions == []


@pytest.mark.asyncio
async def test_apply_catch_succeeds_in_catch_window() -> None:
    """Catch window открыт — happy path с явной now_utc.

    Окно 09:00-21:00 MSK, club_date=2026-08-18.
    now = 22:00 MSK 18 aug = 19:00 UTC 18 aug — внутри catch window.
    """
    habit_repo = FakeHabitRepo()
    habit = _make_habit_with_window(
        start=time(9, 0), end=time(21, 0), penalty_amount=100
    )
    habit_repo.add(habit)

    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))

    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))

    session = _NoStreakSession()
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    now_utc = datetime(2026, 8, 18, 19, 0, tzinfo=ZoneInfo("UTC"))
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=date(2026, 8, 18),
        catcher_membership_id=str(uuid4()),
        now_utc=now_utc,
    )
    assert penalty.amount == 100


@pytest.mark.asyncio
async def test_apply_catch_rejects_after_topup_late_catch() -> None:
    """После закрытия catch window топ депозита НЕ открывает ловлю заново.

    Сценарий: catch window для club_date=18 закрылся в 04:00 UTC 19 aug.
    В 05:00 UTC 19 aug юзер топит депозит. В 05:30 UTC 19 aug кто-то пытается
    поймать. CatchWindowClosedError, НЕ списание.
    """
    habit_repo = FakeHabitRepo()
    habit = _make_habit_with_window(
        start=time(9, 0), end=time(21, 0), penalty_amount=100
    )
    habit_repo.add(habit)

    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))

    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))  # уже пополнил

    session = _NoStreakSession()
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    now_utc = datetime(2026, 8, 19, 5, 30, tzinfo=ZoneInfo("UTC"))
    with pytest.raises(CatchWindowClosedError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 8, 18),  # вчера
            catcher_membership_id=str(uuid4()),
            now_utc=now_utc,
        )
    assert session.penalties == []
    assert session.transactions == []


@pytest.mark.asyncio
async def test_apply_catch_rejects_when_time_crosses_boundary_after_lock() -> None:
    """Pravki-manual-catch-2026-08-18 §Шаг 2 v2: race-free резолюция now_utc.

    Моделирует пограничную гонку:
    1. Ловец нажал «Поймать» в момент T0, который ВНУТРИ catch window.
    2. Запрос вошёл в apply_catch, начал ждать user-lock (параллельно
       идёт topup жертвы).
    3. Lock получен в момент T1, где T1 > catch_window_end (окно закрылось).
    4. Старая логика резолвила now_utc на входе в функцию (T0) и
       проверка проходила — штраф списывался после границы.
    5. Новая логика (now_utc резолвится ПОД lock'ом, после всех
       defense-in-depth проверок) использует now_utc=T1 и отвергает.

    Тест моделирует (1) передачей now_utc, который на 1 секунду позже
    catch_window_end — эквивалент ситуации "lock был задержан".
    Контракт: даже если catcher's request был валиден в момент старта,
    если момент после lock-acquisition пересёк границу, apply_catch
    возвращает CatchWindowClosedError без побочных эффектов:
    - нет Penalty в БД (session.penalties)
    - нет Transaction (session.transactions)
    - deposit_balance не изменён
    """
    habit_repo = FakeHabitRepo()
    habit = _make_habit_with_window(
        start=time(9, 0), end=time(21, 0), penalty_amount=100
    )
    habit_repo.add(habit)

    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))

    user_repo = FakeUserRepo()
    initial_deposit = 500
    user_repo.add(_make_user(id=1, deposit_balance=initial_deposit))

    session = _NoStreakSession()
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    # now_utc на 1 секунду ПОЗЖЕ catch_window_end. Эмулирует ситуацию
    # "ловцу пришлось ждать lock 1 секунду, и catch window закрылся".
    catch_end_utc = habit.catch_window_end(date(2026, 8, 18))
    now_utc_after_lock = catch_end_utc + timedelta(seconds=1)
    assert now_utc_after_lock > catch_end_utc, "precondition"

    # Ловим CatchWindowClosedError.
    with pytest.raises(CatchWindowClosedError):
        await service.apply_catch(
            catcher_user_id=2,
            violator_membership_id=str(violator.id),
            club_date=date(2026, 8, 18),
            catcher_membership_id=str(uuid4()),
            now_utc=now_utc_after_lock,
        )

    # КРИТИЧЕСКИЕ инварианты: ничего финансового не произошло.
    assert session.penalties == [], "Penalty не должен быть создан"
    assert session.transactions == [], "Transaction не должна быть создана"

    # Deposit не изменён.
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == initial_deposit, (
        f"Deposit должен остаться {initial_deposit}, "
        f"не {violator_user.deposit_balance}"
    )

    # Lock_for_update был вызван (race-fix semantic: lock acquired перед
    # time check).
    assert user_repo._lock_calls == [1], (
        "lock_for_update(user_id=1) должен быть вызван до time check"
    )


# ---------------------------------------------------------------------------
# Pravki-manual-catch-2026-08-18 §Шаг 3 (Commit 1): deprecate auto-charge
# ---------------------------------------------------------------------------
#
# `apply_window_expired` и `mark_waived_unable_to_pay` теперь safe no-op
# (deprecated). Сохраняем по одному тесту на метод, который проверяет
# no-op контракт:
# - возвращает ожидаемый безопасный результат;
# - не создаёт Penalty;
# - не создаёт Transaction;
# - не меняет депозит.
#
# Эти методы могут быть вызваны из старых Celery-сообщений в брокере
# или из других неучтённых callers — они не должны падать.


@pytest.mark.asyncio
async def test_apply_window_expired_no_op_returns_none_no_side_effects() -> None:
    """Pravki-manual-catch-2026-08-18 §Шаг 3 (Commit 1).

    DEPRECATED метод `apply_window_expired` теперь safe no-op:
    возвращает None, не пишет Penalty/Transaction, не меняет deposit.
    """
    from app.services.penalty_service import PenaltyService

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    initial_deposit = 500
    user_repo.add(_make_user(id=1, deposit_balance=initial_deposit))

    session = _NoStreakSession()
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    # Метод вызван с любыми аргументами — no-op поведение.
    result = await service.apply_window_expired(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )

    assert result is None, "DEPRECATED метод должен возвращать None"
    assert session.penalties == [], "no-op не должен создавать Penalty"
    assert session.transactions == [], "no-op не должен создавать Transaction"

    # Deposit не изменён.
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == initial_deposit, (
        f"deposit должен остаться {initial_deposit}"
    )

    # Prize pool не инкрементирован.
    habit_after = await habit_repo.get(str(habit.id))
    assert habit_after is not None
    assert habit_after.prize_pool == 0, "no-op не должен инкрементить prize_pool"


@pytest.mark.asyncio
async def test_mark_waived_unable_to_pay_no_op_returns_none_no_side_effects() -> None:
    """Pravki-manual-catch-2026-08-18 §Шаг 3 (Commit 1).

    DEPRECATED метод `mark_waived_unable_to_pay` теперь safe no-op:
    возвращает None, не пишет Penalty/Transaction.
    """
    from app.services.penalty_service import PenaltyService

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.PAUSED
    )
    user_repo = FakeUserRepo()
    initial_deposit = 0
    user_repo.add(_make_user(id=1, deposit_balance=initial_deposit))

    session = _NoStreakSession()
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
        redis_port=_NoopLimiter(),
    )

    # Метод вызван с любыми аргументами — no-op поведение.
    result = await service.mark_waived_unable_to_pay(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )

    assert result is None, "DEPRECATED метод должен возвращать None"
    assert session.penalties == [], "no-op не должен создавать Penalty"
    assert session.transactions == [], "no-op не должен создавать Transaction"

    # Deposit не изменён (хотя у PAUSED он и так 0).
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == initial_deposit

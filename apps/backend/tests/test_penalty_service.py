from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    TransactionType,
)
from app.core.exceptions import (
    CannotCatchSelfError,
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


@pytest.mark.asyncio
async def test_apply_window_expired_writes_waived_marker_when_deposit_zero() -> None:
    """Pravki-no-deposit-waived-marker (разведка 2026-08-16): при deposit=0
    apply_window_expired пишет маркер Penalty(reason=WAIVED_UNABLE_TO_PAY,
    amount=0) вместо silent return None.

    Без этого маркера день остаётся «непомеченным» в БД → apply_catch
    после topup юзера списывает деньги за уже прошедший день (финансовая
    дыра: «человек в гневе, что развод»).

    Контракт:
    - Возвращает None (caller `close_catch_window` не уведомляет, в
      `penalized` не инкрементирует — это не реальный штраф, а маркер).
    - Создаёт ровно 1 Penalty с reason=WAIVED_UNABLE_TO_PAY, amount=0.
    - НЕ создаёт Transaction (нет финансового события).
    - НЕ вызывает recompute_pause_status (баланс не менялся).
    - user.deposit_balance остаётся 0.
    - habit.prize_pool остаётся 0.
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 1000  # больше deposit, чтобы amount <= 0
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))

    session = _NoStreakSession()
    checkin_repo = FakeCheckinRepo()

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    result = await service.apply_window_expired(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )

    # Возвращаем None — caller не должен уведомлять / инкрементить.
    assert result is None

    # Ровно 1 Penalty с правильными полями.
    assert len(session.penalties) == 1, (
        f"Ожидали 1 маркерную Penalty, получили {len(session.penalties)}"
    )
    waived = session.penalties[0]
    assert waived.reason == PenaltyReason.WAIVED_UNABLE_TO_PAY
    assert waived.amount == 0
    assert waived.fund_share == 0
    assert waived.catcher_membership_id is None
    assert waived.catcher_bonus_points == 0
    assert waived.bonus_applied is False
    assert waived.membership_id == str(violator.id)
    assert waived.date == date(2026, 1, 1)

    # НИКАКИХ финансовых последствий.
    assert session.transactions == [], (
        "Transaction(amount=0) НЕ должен создаваться — это не финансовое событие"
    )
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 0, (
        "deposit не должен меняться в WAIVED-ветке"
    )
    habit_after = await habit_repo.get(str(habit.id))
    assert habit_after is not None
    assert habit_after.prize_pool == 0, (
        "prize_pool не должен инкрементиться в WAIVED-ветке"
    )


class _SessionWithExistingPenaltyCheck(_NoStreakSession):
    """Fake-сессия: execute() возвращает ранее добавленные Penalty.

    Нужна для теста идемпотентности apply_window_expired. Без этого
    _NoStreakSession.execute() всегда возвращает пустой результат →
    existing-check проходит → второй вызов создал бы дубль WAIVED.
    В реальной Postgres дубль блокируется UNIQUE-индексом
    uq_penalty_per_day_reason, но fake-сессия его не симулирует —
    отсюда необходимость в этом хелпере.
    """

    def __init__(self) -> None:
        super().__init__()
        self._existing_penalties: list[Penalty] = []

    def add(self, obj: Any) -> None:
        super().add(obj)
        if isinstance(obj, Penalty):
            self._existing_penalties.append(obj)

    async def execute(self, stmt: Any) -> Any:
        # existing-check в apply_window_expired (lines 244-252) делает
        # SELECT из penalties — возвращаем последнюю добавленную как
        # «найденную существующую». В реальной БД это была бы та же
        # строка из предыдущей транзакции.
        existing = (
            self._existing_penalties[-1] if self._existing_penalties else None
        )

        class _Result:
            def first(self_inner) -> Any:
                return existing

            def all(self_inner) -> list:
                return list(self._existing_penalties)

        return _Result()


@pytest.mark.asyncio
async def test_apply_window_expired_idempotent_after_waived_marker() -> None:
    """Идемпотентность: второй вызов apply_window_expired для того же
    (membership, date) при deposit=0 НЕ создаёт дубль маркера.

    Сценарий из прод-реальности: cron `close_catch_window` запускается
    ежечасно (:05) и вызывает apply_window_expired для каждого активного
    member'а без чек-ина. Если по любой причине вызов повторился в
    течение часа (retry, несколько воркеров, ручной restart) — второй
    вызов должен быть no-op, иначе получим 2 WAIVED-записи и
    PenaltyAlreadyProcessedError в apply_catch из-за UNIQUE-конфликта.

    Контракт:
    - Оба вызова возвращают None.
    - Ровно 1 Penalty в session (не 2).
    - 0 Transactions (no-op).
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 1000
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))

    session = _SessionWithExistingPenaltyCheck()

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    # Первый вызов — создаёт маркер.
    result1 = await service.apply_window_expired(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )
    assert result1 is None
    assert len(session.penalties) == 1
    assert session.penalties[0].reason == PenaltyReason.WAIVED_UNABLE_TO_PAY

    # Второй вызов — existing-check ловит маркер из первого вызова
    # через _SessionWithExistingPenaltyCheck.execute() → return None
    # ДО ветки amount <= 0. Никакого дубля.
    result2 = await service.apply_window_expired(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )
    assert result2 is None
    assert len(session.penalties) == 1, (
        f"Идемпотентность нарушена: ожидали 1 WAIVED-маркер, "
        f"получили {len(session.penalties)} дублей"
    )
    assert session.transactions == []


class _SessionWithDateFilteredExistingCheck(_NoStreakSession):
    """Fake session: execute() возвращает Penalty, совпадающую по дате из WHERE.

    Расширение _SessionWithExistingPenaltyCheck: при наличии нескольких
    Penalty в `_existing_penalties` возвращает только ту, чей `date` равен
    значению из `Penalty.date == X` в WHERE clause. Это имитирует реальный
    SELECT, который фильтрует по дате.

    Нужно для теста apply_catch с WAIVED за ВЧЕРА + catch за СЕГОДНЯ:
    вчерашний маркер не должен ловить сегодняшний запрос.

    Также корректно обрабатывает JOIN-запрос MembershipService.recompute_pause_status
    (select(Membership, Habit.penalty_amount).join(...)) — для таких запросов
    возвращает пустой результат, как и _NoStreakSession (recompute в тестах
    без надобности, реальная логика покрыта интеграционными тестами).
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
async def test_apply_catch_rejected_when_waived_marker_exists() -> None:
    """WAIVED_UNABLE_TO_PAY за club_date → apply_catch отвергается.

    Сценарий из разведки 2026-08-16: после того как apply_window_expired
    создал WAIVED-маркер (юзер не мог платить), юзер топит депозит.
    Другой участник пытается поймать его за тот же день → должен быть
    reject, иначе деньги списываются повторно (финансовая дыра).
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=500))  # юзер уже пополнил

    session = _SessionWithDateFilteredExistingCheck()
    # Имитируем, что apply_window_expired ранее создал WAIVED за сегодня.
    waived = Penalty(
        id=str(uuid4()),
        membership_id=str(violator.id),
        catcher_membership_id=None,
        amount=0,
        fund_share=0,
        catcher_bonus_points=0,
        reason=PenaltyReason.WAIVED_UNABLE_TO_PAY,
        date=date(2026, 1, 1),
        bonus_applied=False,
    )
    session.add(waived)

    service = PenaltyService(
        session=session,
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
        )

    # Код ошибки — единый для всех reason'ов (см. Q2 в Pravki-no-deposit-waived-marker.md).
    assert exc_info.value.code == "penalty_already_processed", (
        f"Ожидали penalty_already_processed, получили {exc_info.value.code}"
    )

    # Никаких новых Penalty не создалось.
    assert len(session.penalties) == 1, (
        f"Должна быть только WAIVED, получили {len(session.penalties)}"
    )
    # Баланс жертвы не тронут.
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 500, "deposit не должен меняться"


@pytest.mark.asyncio
async def test_apply_catch_rejected_when_window_closed_penalty_exists() -> None:
    """Бонус-закрытие дыры: WINDOW_CLOSED_NO_CATCH за club_date →
    apply_catch отвергается.

    До расширения идемпотентности фильтр `reason == CAUGHT` пропускал
    этот случай (reason отличался), и UNIQUE-индекс uq_penalty_per_day_reason
    тоже не срабатывал. Прямой POST /catch в обход UI can_catch=False
    списывал штраф дважды.

    UI уже блокирует через can_catch=False (penalty_set содержит WINDOW_CLOSED),
    но defense-in-depth на уровне сервиса обязателен.
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
    # Имитируем, что cron close_catch_window ранее создал WINDOW_CLOSED_NO_CATCH.
    window_penalty = Penalty(
        id=str(uuid4()),
        membership_id=str(violator.id),
        catcher_membership_id=None,
        amount=habit.penalty_amount,
        fund_share=habit.penalty_amount,
        catcher_bonus_points=0,
        reason=PenaltyReason.WINDOW_CLOSED_NO_CATCH,
        date=date(2026, 1, 1),
        bonus_applied=False,
    )
    session.add(window_penalty)

    service = PenaltyService(
        session=session,
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
        )

    assert exc_info.value.code == "penalty_already_processed"

    # Никаких новых Penalty — только существующая WINDOW_CLOSED_NO_CATCH.
    assert len(session.penalties) == 1
    assert session.penalties[0].reason == PenaltyReason.WINDOW_CLOSED_NO_CATCH
    # Баланс жертвы не тронут (это и есть закрытие дыры — без reject'а
    # deposit ушёл бы на -penalty).
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 500


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
        )

    # Никаких новых Penalty, баланс не тронут.
    assert len(session.penalties) == 1
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 500


@pytest.mark.asyncio
async def test_apply_catch_succeeds_for_other_date_when_waived_marker_for_previous_day() -> None:
    """WAIVED за ВЧЕРА + catch за СЕГОДНЯ → catch УСПЕШЕН.

    Проверяет, что расширение идемпотентности не делает over-reject:
    каждый клуб-день независим. Маркер за вчерашний день НЕ должен
    блокировать catch за сегодня.
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
    # Имитируем, что вчера apply_window_expired создал WAIVED.
    yesterday_waived = Penalty(
        id=str(uuid4()),
        membership_id=str(violator.id),
        catcher_membership_id=None,
        amount=0,
        fund_share=0,
        catcher_bonus_points=0,
        reason=PenaltyReason.WAIVED_UNABLE_TO_PAY,
        date=date(2026, 1, 1),  # вчера
        bonus_applied=False,
    )
    session.add(yesterday_waived)

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    # Catch за СЕГОДНЯ (другой день) — должен пройти.
    penalty = await service.apply_catch(
        catcher_user_id=2,
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 2),  # сегодня
        catcher_membership_id=str(uuid4()),
    )

    assert penalty.reason == PenaltyReason.CAUGHT
    assert penalty.amount == habit.penalty_amount
    # Теперь 2 Penalty: вчерашний WAIVED + сегодняшний CAUGHT.
    assert len(session.penalties) == 2
    # Баланс списан за сегодняшний catch.
    violator_user = await user_repo.get(1)
    assert violator_user is not None
    assert violator_user.deposit_balance == 500 - habit.penalty_amount


@pytest.mark.asyncio
async def test_mark_waived_unable_to_pay_creates_marker_for_paused() -> None:
    """Pravki-no-deposit-waived-marker (коммит A 2026-08-17):
    mark_waived_unable_to_pay для PAUSED юзера → создаётся маркер.

    Контракт:
    - Возвращает Penalty (не None).
    - reason=WAIVED_UNABLE_TO_PAY, amount=0.
    - НЕ создаёт Transaction (no-op для финансов).
    - НЕ вызывает recompute_pause_status (баланс не менялся).
    - membership_id, date установлены правильно.
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    # Явно выставляем PAUSED (add_for по умолчанию ACTIVE).
    violator.status = MembershipStatus.PAUSED
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))  # Wide: deposit может быть любым

    session = _NoStreakSession()

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    marker = await service.mark_waived_unable_to_pay(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )

    assert marker is not None
    assert marker.reason == PenaltyReason.WAIVED_UNABLE_TO_PAY
    assert marker.amount == 0
    assert marker.fund_share == 0
    assert marker.catcher_membership_id is None
    assert marker.catcher_bonus_points == 0
    assert marker.bonus_applied is False
    assert marker.membership_id == str(violator.id)
    assert marker.date == date(2026, 1, 1)

    # Никаких финансовых последствий.
    assert session.transactions == [], (
        "WAIVED-маркер не должен создавать Transaction"
    )
    user_after = await user_repo.get(1)
    assert user_after is not None
    assert user_after.deposit_balance == 0, (
        "deposit_balance не должен меняться в mark_waived_unable_to_pay"
    )


@pytest.mark.asyncio
async def test_mark_waived_unable_to_pay_skips_active_membership() -> None:
    """Defensive: ACTIVE membership — НЕ наш случай. Возвращает None,
    ничего не пишет.

    ACTIVE должен идти через apply_window_expired, не через эту функцию.
    Если кто-то случайно вызовет mark_waived_unable_to_pay для ACTIVE —
    мы не должны создавать WAIVED-маркер (иначе ломается контракт:
    для ACTIVE+deposit>=penalty списываем полный штраф, а не маркер).
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    # По умолчанию add_for ставит ACTIVE — оставляем.
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
    )

    result = await service.mark_waived_unable_to_pay(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )

    assert result is None, "ACTIVE должен skip'аться"
    assert session.penalties == [], (
        "Для ACTIVE ничего не пишем — это контракт apply_window_expired"
    )
    assert session.transactions == []


@pytest.mark.asyncio
async def test_mark_waived_unable_to_pay_skips_existing_marker() -> None:
    """Идемпотентность: existing WAIVED за день → return None, не создаём дубль.

    Сценарий: cron retry (второй запуск в течение часа для того же клуба) —
    не должен создавать вторую WAIVED-запись. existing-check ловит.
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    violator.status = MembershipStatus.PAUSED
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))

    session = _SessionWithDateFilteredExistingCheck()
    # Имитируем уже созданный WAIVED от предыдущего cron-запуска.
    existing_waived = Penalty(
        id=str(uuid4()),
        membership_id=str(violator.id),
        catcher_membership_id=None,
        amount=0,
        fund_share=0,
        catcher_bonus_points=0,
        reason=PenaltyReason.WAIVED_UNABLE_TO_PAY,
        date=date(2026, 1, 1),
        bonus_applied=False,
    )
    session.add(existing_waived)

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    result = await service.mark_waived_unable_to_pay(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )

    assert result is None
    assert len(session.penalties) == 1, (
        f"Идемпотентность нарушена: ожидали 1 маркер, получили {len(session.penalties)}"
    )


@pytest.mark.asyncio
async def test_mark_waived_unable_to_pay_skips_existing_caught_penalty() -> None:
    """Race-защита: если PAUSED юзер был пойман между моментом когда
    определился статус и моментом когда cron пишет маркер — existing
    CAUGHT за день ловится, return None (нет дубля).

    Без этой защиты: cron мог бы создать WAIVED поверх CAUGHT, нарушая
    уникальный UNIQUE uq_penalty_per_day_reason.
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    violator.status = MembershipStatus.PAUSED  # уже PAUSED
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=0))

    session = _SessionWithDateFilteredExistingCheck()
    # Race: catcher уже успел создать CAUGHT за день.
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

    result = await service.mark_waived_unable_to_pay(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )

    assert result is None
    assert len(session.penalties) == 1, "Не должно быть дубля поверх CAUGHT"


@pytest.mark.asyncio
async def test_mark_waived_unable_to_pay_works_with_partial_deposit() -> None:
    """Wide-семантика: PAUSED с deposit=24000 (penalty=25000) — тоже
    получает маркер. Юзер не может позволить штраф → прощаем полностью.

    Это закрывает сценарий "закончились почти все деньги, кроме 100₽" —
    раньше такой юзер был PAUSED (deposit < penalty), но если бы мы
    требовали strict deposit==0 для маркера, то дыра оставалась бы
    открытой: catch через direct API после topup всё равно списал бы
    полный penalty.
    """
    from app.core.constants import PenaltyReason

    habit_repo = FakeHabitRepo()
    habit = make_habit()
    habit.penalty_amount = 25000  # больше deposit, юзер PAUSED
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    violator = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    violator.status = MembershipStatus.PAUSED
    user_repo = FakeUserRepo()
    user_repo.add(_make_user(id=1, deposit_balance=24000))  # частичный депозит

    session = _NoStreakSession()

    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=FakeCheckinRepo(),
        suspicious_repo=FakeSuspiciousPairsRepository(),
        user_repo=user_repo,
    )

    marker = await service.mark_waived_unable_to_pay(
        violator_membership_id=str(violator.id),
        club_date=date(2026, 1, 1),
    )

    assert marker is not None
    assert marker.reason == PenaltyReason.WAIVED_UNABLE_TO_PAY
    assert marker.amount == 0
    assert session.transactions == [], "Wide: deposit не списываем"
    user_after = await user_repo.get(1)
    assert user_after is not None
    assert user_after.deposit_balance == 24000, "deposit остаётся нетронутым"


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

    # Session с refresh(), который перечитывает атрибуты из membership_repo
    # (имитирует SQLAlchemy session.refresh(obj) — повторный SELECT).
    class _SessionWithRefresh(_NoStreakSession):
        def __init__(self, m_repo: FakeMembershipRepo) -> None:
            super().__init__()
            self._m_repo = m_repo

        async def refresh(self, obj: Any) -> None:
            """SQLAlchemy session.refresh(obj) — re-query атрибутов из БД.

            В фейке «БД» — это FakeMembershipRepo._store. Перечитываем
            атрибуты obj из store. После race-мутации violator.status
            в store == PAUSED, и это проброшено через refresh.
            """
            fresh = await self._m_repo.get(str(obj.id))
            assert fresh is not None
            obj.status = fresh.status

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
        )

    # Belt-and-suspenders: убеждаемся, что НИЧЕГО не было создано в БД
    # (никакого Penalty, никакой Transaction).
    session = service._session
    assert session.penalties == []
    assert session.transactions == []


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

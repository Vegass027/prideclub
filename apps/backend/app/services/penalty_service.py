from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    PenaltyReason,
    TransactionType,
)
from app.core.exceptions import (
    CannotCatchSelfError,
    HabitNotFoundError,
    MembershipNotActiveError,
    PenaltyAlreadyProcessedError,
    TooManyCatchAttemptsError,
)
from app.core.logging import get_logger
from app.core.utils import parse_rate_limit_spec
from app.models.penalty import Penalty
from app.models.transaction import Transaction
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository


class RedisPort(Protocol):
    async def incr_catch(self, catcher_user_id: int) -> int: ...


class PenaltyService:
    """Бизнес-логика штрафов.

    Одна операция = одна транзакция (вызывающий код в worker управляет commit'ом).
    """

    def __init__(
        self,
        session: AsyncSession,
        habit_repo: HabitRepository,
        membership_repo: MembershipRepository,
        checkin_repo: CheckinRepository,
        suspicious_repo: SuspiciousPairsRepository,
        redis_port: RedisPort | None = None,
    ) -> None:
        self._session = session
        self._habit_repo = habit_repo
        self._membership_repo = membership_repo
        self._checkin_repo = checkin_repo
        self._suspicious_repo = suspicious_repo
        self._redis = redis_port
        self._logger = get_logger("penalty_service")

    async def apply_catch(
        self,
        *,
        catcher_user_id: int,
        violator_membership_id: str,
        club_date,
        catcher_membership_id: str | None,
    ) -> Penalty:
        if self._redis is not None:
            count = await self._redis.incr_catch(catcher_user_id)
            if count > parse_rate_limit_spec(PenaltyConfig.RATE_LIMIT_CATCH)[0]:
                raise TooManyCatchAttemptsError()

        violator = await self._membership_repo.lock_for_update(violator_membership_id)
        if catcher_membership_id is not None and catcher_membership_id == violator_membership_id:
            raise CannotCatchSelfError()
        if violator.status != MembershipStatus.ACTIVE:
            raise MembershipNotActiveError()

        habit = await self._habit_repo.get(str(violator.habit_id))
        if habit is None:
            raise HabitNotFoundError()

        # Идемпотентность: уникальный ключ (membership_id, date, reason).
        existing = await self._session.execute(
            Penalty.__table__.select().where(
                Penalty.membership_id == violator_membership_id,
                Penalty.date == club_date,
                Penalty.reason == PenaltyReason.CAUGHT.value,
            )
        )
        if existing.first() is not None:
            raise PenaltyAlreadyProcessedError()

        # Списываем депозит (но не ниже 0).
        amount = min(habit.penalty_amount, violator.deposit_balance)
        if amount <= 0:
            # Депозит исчерпан — membership переходит в paused.
            # НЕ flush'им до raise — rollback откатит изменение. Worker-таска
            # обязана обработать "deposit_exhausted" в отдельной транзакции
            # (см. worker/tasks/process_penalty.py: ловит этот код и ставит
            # membership в PAUSED отдельным коммитом).
            violator.status = MembershipStatus.PAUSED
            raise PenaltyAlreadyProcessedError("deposit_exhausted", code="deposit_exhausted")

        violator.deposit_balance -= amount
        await self._habit_repo.add_to_prize_pool(str(habit.id), amount)

        # Применяется ли кэтчер-бонус — отдельная проверка suspicious_pairs (см. apply_catch_bonus).
        grant_catcher_bonus = not await self._suspicious_repo.lookup_flagged(
            catcher_membership_id, violator_membership_id
        )

        penalty = Penalty(
            id=str(uuid4()),
            membership_id=violator_membership_id,
            catcher_membership_id=catcher_membership_id if grant_catcher_bonus else None,
            amount=amount,
            fund_share=amount,
            catcher_bonus_points=PenaltyConfig.CATCHER_BONUS_POINTS if grant_catcher_bonus else 0,
            reason=PenaltyReason.CAUGHT,
            date=club_date,
            bonus_applied=False,
        )
        self._session.add(penalty)

        # Flush penalty ПЕРЕД добавлением transaction — Postgres FK
        # `transactions.related_penalty_id → penalties.id` проверяется
        # per-statement, и INSERT transaction в той же flush() видит ещё
        # не зафиксированный penalty → ForeignKeyViolationError.
        # Сначала flush'им penalty (INSERT + RETURNING), затем transaction.
        await self._session.flush()

        transaction = Transaction(
            id=str(uuid4()),
            user_id=violator.user_id,
            type=TransactionType.PENALTY.value,
            amount=-amount,
            balance_after=violator.deposit_balance,
            related_penalty_id=penalty.id,
            related_membership_id=violator_membership_id,
        )
        self._session.add(transaction)

        if violator.deposit_balance == 0:
            violator.status = MembershipStatus.PAUSED

        await self._session.flush()

        self._logger.info(
            "penalty_caught",
            extra={
                "violator_membership_id": violator_membership_id,
                "catcher_membership_id": catcher_membership_id,
                "amount": amount,
                "habit_id": str(habit.id),
                "club_date": str(club_date),
            },
        )
        return penalty

    async def apply_window_expired(
        self, *, violator_membership_id: str, club_date
    ) -> Penalty | None:
        """Штраф за пропуск без улова: cron close_catch_window.

        Идемпотентность обеспечивается INSERT ON CONFLICT DO NOTHING через
        уникальный индекс (membership_id, date, reason).
        """
        violator = await self._membership_repo.lock_for_update(violator_membership_id)
        if violator.status != MembershipStatus.ACTIVE:
            return None

        habit = await self._habit_repo.get(str(violator.habit_id))
        if habit is None:
            return None

        # Если уже есть штраф за сегодня — идемпотентный no-op.
        existing = await self._session.execute(
            Penalty.__table__.select().where(
                Penalty.membership_id == violator_membership_id,
                Penalty.date == club_date,
            )
        )
        if existing.first() is not None:
            return None

        amount = min(habit.penalty_amount, violator.deposit_balance)
        if amount <= 0:
            violator.status = MembershipStatus.PAUSED
            return None

        violator.deposit_balance -= amount
        await self._habit_repo.add_to_prize_pool(str(habit.id), amount)

        penalty = Penalty(
            id=str(uuid4()),
            membership_id=violator_membership_id,
            catcher_membership_id=None,
            amount=amount,
            fund_share=amount,
            catcher_bonus_points=0,
            reason=PenaltyReason.WINDOW_CLOSED_NO_CATCH,
            date=club_date,
            bonus_applied=False,
        )
        self._session.add(penalty)
        # Flush перед transaction — см. apply_catch().
        await self._session.flush()

        transaction = Transaction(
            id=str(uuid4()),
            user_id=violator.user_id,
            type=TransactionType.PENALTY.value,
            amount=-amount,
            balance_after=violator.deposit_balance,
            related_penalty_id=penalty.id,
            related_membership_id=violator_membership_id,
        )
        self._session.add(transaction)

        if violator.deposit_balance == 0:
            violator.status = MembershipStatus.PAUSED

        await self._session.flush()
        self._logger.info(
            "penalty_window_expired",
            extra={
                "violator_membership_id": violator_membership_id,
                "amount": amount,
                "habit_id": str(habit.id),
            },
        )
        return penalty
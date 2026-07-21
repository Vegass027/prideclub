from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.core.exceptions import (
    CheckinAlreadyExistsError,
    CheckinWindowClosedError,
    HabitArchivedError,
    HabitNotFoundError,
    MembershipNotFoundError,
    MembershipNotActiveError,
)
from app.core.logging import get_logger
from app.models.checkin import Checkin
from app.models.habit import Habit
from app.models.membership import Membership
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.services.proof_validator import ProofMessage, validate_proof_media


class CachePort(Protocol):
    """Redis-порт: ровно один метод — инвалидировать статус дня."""

    async def invalidate_today(self, habit_id: str, membership_id: str) -> None: ...


class CheckinService:
    """Бизнес-логика чек-ина.

    Все запросы — через репозитории, кэш — через порт CachePort.
    """

    def __init__(
        self,
        session,
        habit_repo: HabitRepository,
        membership_repo: MembershipRepository,
        checkin_repo: CheckinRepository,
        cache: CachePort | None = None,
    ) -> None:
        self._session = session
        self._habit_repo = habit_repo
        self._membership_repo = membership_repo
        self._checkin_repo = checkin_repo
        self._cache = cache
        self._logger = get_logger("checkin_service")

    async def process_checkin(
        self,
        *,
        user_id: int,
        habit_id: str,
        proof: ProofMessage,
        proof_message_id: int,
        now_utc: datetime,
    ) -> tuple[Checkin, bool]:
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()

        membership = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if membership is None:
            raise MembershipNotFoundError()
        if membership.status.value != "active":
            raise MembershipNotActiveError()

        # Антифрод — медиа должно соответствовать типу привычки.
        # proof.proof_type приходит из бота и проверяется здесь как
        # независимый от habit источник.
        validate_proof_media(proof, max_age_seconds=60)
        if proof.proof_type.value != habit.proof_type.value:
            from app.services.proof_validator import ProofValidationError

            raise ProofValidationError("wrong_type")

        # Окно чек-ина — в TZ клуба.
        if not habit.is_within_checkin_window(now_utc):
            raise CheckinWindowClosedError()

        club_date = habit.club_date(now_utc)

        try:
            checkin, created = await self._checkin_repo.get_or_create_done(
                membership_id=membership.id,
                on_date=club_date,
                proof_message_id=proof_message_id,
            )
        except Exception as exc:  # noqa: BLE001
            from sqlalchemy.exc import IntegrityError

            if isinstance(exc, IntegrityError):
                raise CheckinAlreadyExistsError() from exc
            raise

        if not created:
            # Уже был чек-ин сегодня — это идемпотентный ответ, не ошибка.
            self._logger.info(
                "checkin_duplicate",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "date": str(club_date),
                },
            )

        if self._cache is not None:
            try:
                await self._cache.invalidate_today(habit.id, membership.id)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "cache_invalidate_failed",
                    extra={"err": str(exc), "habit_id": habit_id},
                )

        self._logger.info(
            "checkin_processed",
            extra={
                "user_id": user_id,
                "habit_id": habit_id,
                "membership_id": membership.id,
                "date": str(club_date),
                "created": created,
            },
        )
        return checkin, created

    async def get_today_status(
        self, *, user_id: int, habit_id: str, now_utc: datetime
    ) -> tuple[Habit, Membership, str, int]:
        """Возвращает (habit, membership, status, streak_days)."""
        habit = await self._habit_repo.get(habit_id)
        if habit is None or habit.archived_at is not None:
            raise HabitArchivedError()
        membership = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if membership is None:
            raise MembershipNotFoundError()

        club_date = habit.club_date(now_utc)
        existing = await self._checkin_repo.get_for_date(membership.id, club_date)

        if existing is not None:
            status = existing.status.value
        else:
            window_open = habit.is_within_checkin_window(now_utc)
            if window_open:
                status = "pending"
            else:
                status = "missed"

        streak = await self._compute_streak(membership.id, club_date)
        return habit, membership, status, streak

    async def _compute_streak(self, membership_id: str, up_to) -> int:
        """Считаем серию done-чекинов до up_to.

        Дешёвая реализация: один запрос в БД.
        """
        from sqlalchemy import select

        from app.models.checkin import Checkin

        result = await self._session.execute(
            select(Checkin.date)
            .where(
                Checkin.membership_id == membership_id,
                Checkin.date <= up_to,
                Checkin.status == "done",
            )
            .order_by(Checkin.date.desc())
            .limit(90)
        )
        dates = [row[0] for row in result.all()]
        if not dates:
            return 0
        streak = 0
        expected = up_to
        from datetime import timedelta

        for d in dates:
            if d == expected:
                streak += 1
                expected = expected - timedelta(days=1)
            else:
                break
        return streak
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MembershipStatus
from app.core.exceptions import (
    HabitMemberLimitReachedError,
    HabitNotFoundError,
    InsufficientDepositError,
    MembershipNotActiveError,
    MembershipNotFoundError,
    UserNotFoundError,
)
from app.core.logging import get_logger
from app.models.habit import Habit
from app.models.membership import Membership
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository


class MembershipService:
    def __init__(
        self,
        session: AsyncSession,
        membership_repo: MembershipRepository,
        habit_repo: HabitRepository | None = None,
        user_repo: UserRepository | None = None,
    ) -> None:
        self._session = session
        self._habit_repo = habit_repo
        self._membership_repo = membership_repo
        self._user_repo = user_repo or UserRepository(session)
        self._logger = get_logger("membership_service")

    async def join(self, *, user_id: int, habit_id: str) -> Membership:
        if self._habit_repo is None:
            raise RuntimeError(
                "MembershipService.join requires habit_repo; "
                "construct with habit_repo=HabitRepository(session) for join operations"
            )
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()

        # Z-3.1 (Variant B, согласовано): проверка депозита ВСЕГДА — для нового
        # участника, для возобновления LEFT→ACTIVE, и для реактивации PAUSED→ACTIVE.
        # Никаких исключений «уже был раньше» — deposit всегда проверяется.
        # Если денег не хватает — 403 InsufficientDepositError с суммами для UI.
        # Делаем ПЕРЕД membership-lookup'ом: не имеет смысла лочить membership,
        # если денег всё равно нет.
        user = await self._user_repo.get(user_id)
        if user is None:
            # Юзер без записи в users — крайне вырожденный кейс (race при
            # удалении юзера между TelegramUserDbDep upsert и join).
            # План §Z-3.1 явно указывает UserNotFoundError (НЕ MembershipNotFoundError),
            # чтобы фронт мог различать «юзера нет» vs «клуба/мембершипа нет».
            raise UserNotFoundError()
        if user.deposit_balance < habit.penalty_amount:
            self._logger.info(
                "habit_join_rejected_insufficient_deposit",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "required_kopecks": habit.penalty_amount,
                    "current_kopecks": user.deposit_balance,
                },
            )
            raise InsufficientDepositError(
                required_kopecks=habit.penalty_amount,
                current_kopecks=user.deposit_balance,
                club_penalty_kopecks=habit.penalty_amount,
            )

        # Существующая membership? (ACTIVE/PAUSED/LEFT)
        existing = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if existing is not None:
            if existing.status == MembershipStatus.ACTIVE:
                # Уже в клубе — no-op (идемпотентный join).
                return existing
            # PAUSED или LEFT → реактивируем в ACTIVE.
            # Депозит УЖЕ проверен выше (>= penalty), так что ACTIVE обосновано.
            previous_status = existing.status
            existing.status = MembershipStatus.ACTIVE
            self._logger.info(
                "user_rejoined_habit",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "previous_status": previous_status.value,
                },
            )
            return existing

        # Новый участник — проверяем member_limit под блокировкой строки клуба.
        # FOR UPDATE на habit гарантирует, что счётчик участников и INSERT membership
        # выполняются атомарно относительно других параллельных join.
        if habit.member_limit is not None:
            habit = await self._habit_repo.lock_for_update(habit_id)
            if habit is None:
                # Клуб удалили между гейтом и lock — трактуем как not_found.
                raise HabitNotFoundError()
            active_members = await self._habit_repo.count_active_members(habit_id)
            if active_members >= habit.member_limit:
                self._logger.info(
                    "habit_join_rejected_member_limit",
                    extra={
                        "user_id": user_id,
                        "habit_id": habit_id,
                        "member_limit": habit.member_limit,
                        "active_members": active_members,
                    },
                )
                raise HabitMemberLimitReachedError()

        m = await self._membership_repo.create(user_id=user_id, habit_id=habit_id)
        self._logger.info(
            "user_joined_habit",
            extra={"user_id": user_id, "habit_id": habit_id},
        )
        return m

    async def leave(self, *, user_id: int, habit_id: str) -> Membership:
        m = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if m is None:
            raise MembershipNotFoundError()
        if m.status != MembershipStatus.ACTIVE:
            raise MembershipNotActiveError()
        m.status = MembershipStatus.LEFT
        return m

    async def recompute_pause_status(self, user_id: int) -> None:
        """Пересчитать статус ВСЕХ не-LEFT membership'ов пользователя.

        Pravki-deposit-sse.md §Z-2.5: после любого изменения user.deposit_balance
        (catch в PenaltyService.apply_catch или topup в PaymentService._apply)
        нужно проверить, может ли юзер позволить себе штраф в каждом клубе.

        Логика:
        - Для каждой не-LEFT membership (ACTIVE и PAUSED) сравниваем
          user.deposit_balance с habit.penalty_amount.
        - Если deposit < penalty → PAUSED (юзер не может позволить штраф).
        - Если deposit >= penalty и раньше был PAUSED → ACTIVE (юзер пополнил
          депозит, клуб снова доступен).
        - LEFT не трогаем (явное действие юзера, не автопауза).

        Вызывающий код ДОЛЖЕН держать SELECT FOR UPDATE на user (через
        user_repo.lock_for_update). Под user-lock'ом все параллельные операции
        этого юзера сериализуются → дополнительный lock на memberships не нужен,
        обычные SELECT'ы дадут согласованную картину.

        Не бросает исключений — это best-effort housekeeping. Если юзер не
        существует (вырожденный кейс), выходим молча.
        """
        user = await self._user_repo.get(user_id)
        if user is None:
            return
        deposit = user.deposit_balance

        # Один запрос с JOIN: для каждой не-LEFT membership возвращаем
        # (Membership_obj, habit.penalty_amount). Без N+1.
        rows = (
            await self._session.execute(
                select(Membership, Habit.penalty_amount)
                .join(Habit, Habit.id == Membership.habit_id)
                .where(
                    Membership.user_id == user_id,
                    Membership.status != MembershipStatus.LEFT,
                )
            )
        ).all()

        paused_count = 0
        reactivated_count = 0
        for m, penalty_amount in rows:
            if deposit < penalty_amount and m.status == MembershipStatus.ACTIVE:
                m.status = MembershipStatus.PAUSED
                paused_count += 1
            elif (
                deposit >= penalty_amount
                and m.status == MembershipStatus.PAUSED
            ):
                m.status = MembershipStatus.ACTIVE
                reactivated_count += 1

        if paused_count or reactivated_count:
            self._logger.info(
                "membership_pause_status_recomputed",
                extra={
                    "user_id": user_id,
                    "deposit": deposit,
                    "paused": paused_count,
                    "reactivated": reactivated_count,
                },
            )

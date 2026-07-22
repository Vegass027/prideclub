from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    PenaltyConfig,
    TransactionType,
)
from app.core.logging import get_logger
from app.models.membership import Membership
from app.models.transaction import Transaction
from app.models.user import User
from app.repositories.bonus_rule_repository import BonusRuleRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.penalty_repository import PenaltyRepository
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
from app.repositories.user_repository import UserRepository


class BonusService:
    """Бонусные поинты за уловы и продление подписки.

    По docs/06-data-model §6: bonus_points живёт на users.id, переживает смену клубов.

    Зависимости (DI через конструктор):
    - session: SQLAlchemy AsyncSession (вызывающий код управляет commit)
    - membership_repo: доступ к memberships
    - penalty_repo: lookup Penalty по id
    - user_repo: lookup User по id
    - bonus_rule_repo: lookup BonusRule по (event_type, threshold)
    - suspicious_repo: проверка suspicious_pairs (T2)
    """

    def __init__(
        self,
        session: AsyncSession,
        membership_repo: MembershipRepository,
        penalty_repo: PenaltyRepository,
        user_repo: UserRepository,
        bonus_rule_repo: BonusRuleRepository,
        suspicious_repo: SuspiciousPairsRepository,
    ) -> None:
        self._session = session
        self._membership_repo = membership_repo
        self._penalty_repo = penalty_repo
        self._user_repo = user_repo
        self._bonus_rule_repo = bonus_rule_repo
        self._suspicious_repo = suspicious_repo
        self._logger = get_logger("bonus_service")

    async def apply_catch_bonus(
        self, *, catcher_membership_id: str, penalty_id: str
    ) -> int:
        """Начисляет +1 охотнику за успешный улов.

        Идемпотентность обеспечена `penalty.bonus_applied`.
        """
        penalty_obj = await self._penalty_repo.get(penalty_id)
        if penalty_obj is None or penalty_obj.bonus_applied:
            return 0

        if penalty_obj.catcher_membership_id is None:
            return 0  # suspicious_pair → без бонуса.

        # Доп. проверка: если пара сейчас в flagged — бонус не начислится.
        if await self._suspicious_repo.lookup_flagged(
            penalty_obj.catcher_membership_id, penalty_obj.membership_id
        ):
            self._logger.info(
                "catch_bonus_blocked_by_suspicious_pair",
                extra={
                    "catcher_membership_id": penalty_obj.catcher_membership_id,
                    "violator_membership_id": penalty_obj.membership_id,
                },
            )
            return 0

        catcher_m = await self._membership_repo.get(penalty_obj.catcher_membership_id)
        if catcher_m is None:
            return 0

        user_obj = await self._user_repo.get(catcher_m.user_id)
        if user_obj is None:
            return 0
        user_obj.bonus_points += PenaltyConfig.CATCHER_BONUS_POINTS
        user_obj.bonus_points_updated_at = datetime.now(tz=UTC)

        # Финансовый инвариант (docs/06 §6): penalty.bonus_applied=true → matching
        # transactions row с type=bonus_catch. Без этой записи integrity_check
        # будет ругаться каждый день.
        bonus_transaction = Transaction(
            id=str(uuid4()),
            user_id=user_obj.id,
            type=TransactionType.BONUS_CATCH.value,
            amount=0,
            related_penalty_id=penalty_obj.id,
            related_membership_id=penalty_obj.catcher_membership_id,
        )
        self._session.add(bonus_transaction)

        rule = await self._bonus_rule_repo.find("catch", threshold=5)
        if rule is not None and user_obj.bonus_points % rule.threshold == 0:
            await self._grant_reward(catcher_m, user_obj, rule.reward_value)

        penalty_obj.bonus_applied = True
        # flush() отправляет UPDATE/INSERT в БД; если БД упала — исключение
        # ДОЛЖНО всплыть, чтобы верхний слой (worker task) откатил транзакцию
        # и integrity-check не сработал на ложноположительном bonus_applied=true
        # без соответствующей transactions-строки.
        await self._session.flush()

        self._logger.info(
            "catch_bonus_applied",
            extra={
                "catcher_membership_id": catcher_membership_id,
                "penalty_id": penalty_id,
                "bonus_points": PenaltyConfig.CATCHER_BONUS_POINTS,
            },
        )
        return PenaltyConfig.CATCHER_BONUS_POINTS

    async def _grant_reward(
        self, membership: Membership, user: User, reward_value: int
    ) -> None:
        if membership.auto_renew_enabled:
            # Автоподписка покрывает — копим в накопительные points.
            user.bonus_points += reward_value
            transaction = Transaction(
                id=str(uuid4()),
                user_id=user.id,
                type=TransactionType.BONUS_POINTS.value,
                amount=0,
                related_membership_id=membership.id,
            )
            self._session.add(transaction)
            return

        if membership.subscription_until is None:
            return
        membership.subscription_until = membership.subscription_until + timedelta(days=reward_value)
        transaction = Transaction(
            id=str(uuid4()),
            user_id=user.id,
            type=TransactionType.BONUS_SUBSCRIPTION.value,
            amount=0,
            related_membership_id=membership.id,
            balance_after=membership.deposit_balance,
        )
        self._session.add(transaction)

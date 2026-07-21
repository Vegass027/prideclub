from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    MembershipStatus,
    PenaltyConfig,
    PenaltyReason,
    TransactionType,
)
from app.core.logging import get_logger
from app.models.auxiliary import BonusRule
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.transaction import Transaction
from app.models.user import User
from app.repositories.membership_repository import MembershipRepository


class BonusService:
    """Бонусные поинты за уловы и продление подписки.

    По docs/06-data-model §6: bonus_points живёт на users.id, переживает смену клубов.
    """

    def __init__(self, session: AsyncSession, membership_repo: MembershipRepository) -> None:
        self._session = session
        self._membership_repo = membership_repo
        self._logger = get_logger("bonus_service")

    async def apply_catch_bonus(
        self, *, catcher_membership_id: str, penalty_id: str
    ) -> int:
        """Начисляет +1 охотнику за успешный улов.

        Идемпотентность обеспечена `penalty.bonus_applied`.
        """
        penalty = await self._session.execute(
            select(Penalty).where(Penalty.id == penalty_id)
        )
        penalty_obj = penalty.scalar_one_or_none()
        if penalty_obj is None or penalty_obj.bonus_applied:
            return 0

        if penalty_obj.catcher_membership_id is None:
            return 0  # suspicious_pair → без бонуса.

        catcher = await self._session.execute(
            select(Membership).where(Membership.id == penalty_obj.catcher_membership_id)
        )
        catcher_m = catcher.scalar_one_or_none()
        if catcher_m is None:
            return 0

        user = await self._session.execute(
            select(User).where(User.id == catcher_m.user_id)
        )
        user_obj = user.scalar_one()
        user_obj.bonus_points += PenaltyConfig.CATCHER_BONUS_POINTS
        user_obj.bonus_points_updated_at = datetime.now(tz=timezone.utc)

        rule = await self._find_rule("catch", threshold=5)
        if rule is not None and user_obj.bonus_points % rule.threshold == 0:
            await self._grant_reward(catcher_m, user_obj, rule.reward_value)

        penalty_obj.bonus_applied = True
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

    async def _find_rule(self, event_type: str, *, threshold: int) -> BonusRule | None:
        result = await self._session.execute(
            select(BonusRule).where(
                BonusRule.event_type == event_type,
                BonusRule.threshold == threshold,
            )
        )
        return result.scalar_one_or_none()

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
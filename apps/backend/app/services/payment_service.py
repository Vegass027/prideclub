from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MembershipStatus, TransactionType
from app.core.logging import get_logger
from app.models.membership import Membership
from app.models.transaction import Transaction
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository
from app.services.membership_service import MembershipService


class PaymentService:
    """Идемпотентная обработка платежей Telegram Payments.

    idempotency_key = telegram_payment_charge_id.

    Pravki-deposit-sse.md §Z-2: депозит живёт на users.deposit_balance
    (не на membership). User-level lock сериализует параллельные webhook'и
    (subscription_renewal + deposit_topup одного юзера). После пополнения
    MembershipService.recompute_pause_status пересчитывает статусы всех клубов.

    Если membership ещё не существует (юзер пополнил депозит ДО join), создаём
    её в той же транзакции — для backward-compat с API-контрактом topup'а.
    """

    def __init__(
        self,
        session: AsyncSession,
        user_repo: UserRepository | None = None,
        membership_repo: MembershipRepository | None = None,
        membership_service: MembershipService | None = None,
    ) -> None:
        self._session = session
        self._user_repo = user_repo or UserRepository(session)
        self._membership_repo = membership_repo or MembershipRepository(session)
        self._membership_service = membership_service or MembershipService(
            session=session,
            membership_repo=self._membership_repo,
            user_repo=self._user_repo,
            # habit_repo=None: PaymentService не вызывает join(), а recompute_pause_status
            # использует Habit модель напрямую через JOIN.
        )
        self._logger = get_logger("payment_service")

    async def confirm_subscription(
        self,
        *,
        charge_id: str,
        user_id: int,
        habit_id: str | None,
        amount_kopecks: int,
        months: int,
    ) -> Transaction:
        return await self._apply(
            charge_id=charge_id,
            user_id=user_id,
            kind="subscription",
            amount_kopecks=amount_kopecks,
            habit_id=habit_id,
            extend_days=30 * months,
        )

    async def confirm_deposit_topup(
        self,
        *,
        charge_id: str,
        user_id: int,
        habit_id: str | None,
        amount_kopecks: int,
    ) -> Transaction:
        return await self._apply(
            charge_id=charge_id,
            user_id=user_id,
            kind="deposit_topup",
            amount_kopecks=amount_kopecks,
            habit_id=habit_id,
            extend_days=0,
        )

    async def _apply(
        self,
        *,
        charge_id: str,
        user_id: int,
        kind: str,
        amount_kopecks: int,
        habit_id: str | None,
        extend_days: int,
    ) -> Transaction:
        # 1. Идемпотентность: проверяем, что транзакция с таким ключом уже есть.
        existing = await self._session.execute(
            select(Transaction).where(Transaction.idempotency_key == charge_id)
        )
        existing_tx = existing.scalar_one_or_none()
        if existing_tx is not None:
            self._logger.info(
                "payment_idempotent",
                extra={"charge_id": charge_id, "kind": kind},
            )
            return existing_tx

        # 2. SELECT FOR UPDATE на user. Сериализует параллельные webhook'и
        #    одного юзера (subscription_renewal + deposit_topup одновременно).
        u = await self._user_repo.lock_for_update(user_id)
        if u is None:
            # Юзер не существует — edge-case (topup без initData?). Не падаем,
            # возвращаем явную ошибку через transaction с balance_after=None.
            raise ValueError(f"user {user_id} not found for payment {charge_id}")

        # 3. Membership lookup — только если habit_id указан И существует в БД.
        #    Pravki-deposit-sse.md §Z-2.5: депозит глобальный, habit_id
        #    опционален. Membership-row нужна ТОЛЬКО для related_membership_id
        #    в Transaction. Если юзер ещё не в клубе (или habit_id=None) —
        #    транзакция записывается без привязки (related_membership_id=None,
        #    FK nullable на transactions.related_membership_id).
        m: Membership | None = None
        if habit_id is not None:
            m = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
            # НЕ создаём membership под капотом — это был legacy-хак для
            # старого deposit-на-membership дизайна. PR #1 депозит на user,
            # membership создаётся явно через POST /habits/{id}/join.

        # 4. Применяем эффект. u.deposit_balance += и subscription_until теперь
        #    безопасны — user-строка под FOR UPDATE, никакой параллельный writer
        #    не прочтёт устаревшее значение.
        tx_type = (
            TransactionType.SUBSCRIPTION.value
            if kind == "subscription"
            else TransactionType.DEPOSIT_TOPUP.value
        )
        if kind == "deposit_topup":
            u.deposit_balance += amount_kopecks
        if kind == "subscription" and m is not None:
            # Subscription требует membership — без неё продлить нечего.
            # Если club_id нет, кидаем ошибку (UI: "Сначала вступи в клуб").
            if extend_days and m.subscription_until:
                m.subscription_until = m.subscription_until + timedelta(days=extend_days)
            else:
                m.subscription_until = datetime.utcnow().date() + timedelta(days=extend_days)

        tx = Transaction(
            id=str(uuid4()),
            user_id=user_id,
            type=tx_type,
            amount=amount_kopecks,
            balance_after=u.deposit_balance,
            related_membership_id=m.id if m is not None else None,
            idempotency_key=charge_id,
        )
        self._session.add(tx)

        if m is not None and m.status != MembershipStatus.ACTIVE:
            m.status = MembershipStatus.ACTIVE

        # Пересчёт пауз для всех клубов юзера (Pravki-deposit-sse.md §Z-2.5).
        # После пополнения ранее PAUSED клуб может снова стать ACTIVE.
        await self._membership_service.recompute_pause_status(user_id)

        try:
            await self._session.flush()
        except IntegrityError:
            # Гонка с параллельным webhook'ом — второй вызов получит existing.
            await self._session.rollback()
            existing = await self._session.execute(
                select(Transaction).where(Transaction.idempotency_key == charge_id)
            )
            tx = existing.scalar_one()
            return tx

        self._logger.info(
            "payment_confirmed",
            extra={
                "charge_id": charge_id,
                "user_id": user_id,
                "habit_id": habit_id,
                "kind": kind,
                "amount_kopecks": amount_kopecks,
                "user_deposit_after": u.deposit_balance,
            },
        )
        return tx

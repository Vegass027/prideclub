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


class PaymentService:
    """Идемпотентная обработка платежей Telegram Payments.

    idempotency_key = telegram_payment_charge_id.

    Membership-строка берётся под SELECT ... FOR UPDATE (через
    MembershipRepository.lock_for_update_by_user_habit), чтобы защитить
    `deposit_balance +=` и `subscription_until` от гонки между параллельными
    webhook'ами (например subscription_renewal + deposit_topup, пришедшие
    почти одновременно).

    Если membership ещё не существует, создаём её в той же транзакции и
    повторно лочим (новой select FOR UPDATE видит только-что вставленную
    строку через identity map).
    """

    def __init__(
        self,
        session: AsyncSession,
        membership_repo: MembershipRepository | None = None,
    ) -> None:
        self._session = session
        self._membership_repo = membership_repo or MembershipRepository(session)
        self._logger = get_logger("payment_service")

    async def confirm_subscription(
        self,
        *,
        charge_id: str,
        user_id: int,
        habit_id: str,
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
        habit_id: str,
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
        habit_id: str,
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

        # 2. Достаём membership под FOR UPDATE. Защита от гонки webhook'ов.
        m = await self._membership_repo.lock_for_update_by_user_habit(
            user_id, habit_id
        )
        if m is None:
            # Первый платёж этого пользователя — создаём membership, флашим,
            # затем re-lock для гарантии атомарности относительно других
            # writer'ов, которые могут попытаться сделать то же самое.
            m = Membership(
                user_id=user_id,
                habit_id=habit_id,
                deposit_balance=0,
                subscription_until=None,
            )
            self._session.add(m)
            await self._session.flush()
            # Identity map: повторный lock_for_update вернёт ту же ORM-сущность,
            # но Postgres SELECT FOR UPDATE всё равно наложит row-lock (или
            # обнаружит, что строка уже залочена нашей же транзакцией).
            m = await self._membership_repo.lock_for_update_by_user_habit(
                user_id, habit_id
            )
            # После нашего собственного flush m не может быть None — мы только
            # что его создали. Защитный assert на случай гонки двух процессов.
            assert m is not None, "membership just created must be visible"

        # 3. Применяем эффект. m.deposit_balance += и subscription_until теперь
        # безопасны — строка под FOR UPDATE, никакой параллельный writer не
        # прочтёт устаревшее значение.
        tx_type = (
            TransactionType.SUBSCRIPTION.value
            if kind == "subscription"
            else TransactionType.DEPOSIT_TOPUP.value
        )
        if kind == "deposit_topup":
            m.deposit_balance += amount_kopecks
        if kind == "subscription" and extend_days and m.subscription_until:
            new_until = m.subscription_until + timedelta(days=extend_days)
            m.subscription_until = new_until
        elif kind == "subscription":
            m.subscription_until = datetime.utcnow().date() + timedelta(days=extend_days)

        tx = Transaction(
            id=str(uuid4()),
            user_id=user_id,
            type=tx_type,
            amount=amount_kopecks,
            balance_after=m.deposit_balance,
            related_membership_id=m.id,
            idempotency_key=charge_id,
        )
        self._session.add(tx)

        if m.status != MembershipStatus.ACTIVE:
            m.status = MembershipStatus.ACTIVE

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
                "deposit_balance_after": m.deposit_balance,
            },
        )
        return tx
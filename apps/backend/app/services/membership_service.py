from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import MembershipStatus, TransactionType
from app.core.exceptions import (
    AlreadyActiveError,
    HabitInactiveError,
    HabitMemberLimitReachedError,
    HabitNotFoundError,
    IdempotencyConflictError,
    InsufficientDepositChoiceError,
    InsufficientDepositError,
    MembershipNotActiveError,
    MembershipNotFoundError,
    SubscriptionRequiredError,
)
from app.core.logging import get_logger
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.transaction import Transaction
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

        existing = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if existing is not None:
            if existing.status == MembershipStatus.LEFT:
                # Возобновление: пользователь уже был в клубе, лимит НЕ применяется
                # (иначе бывший член не смог бы вернуться, даже если место освободилось).
                # Z-3.1: депозит тоже НЕ проверяется — если юзер уже был ACTIVE
                # раньше, мы не заставляем его снова платить за вход.
                existing.status = MembershipStatus.ACTIVE
                return existing
            return existing

        # Z-3.1: проверка депозита для нового участника.
        # Применяем ДО member_limit (быстрее отказ если денег нет).
        # Для LEFT→ACTIVE выше — НЕ проверяем.
        user = await self._user_repo.get(user_id)
        if user is None:
            # Юзер без записи в users — крайне вырожденный кейс (race при
            # удалении юзера). Не блокируем, но и не создаём membership
            # неизвестного юзера.
            raise MembershipNotFoundError()
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
        # Pravki-subscribe-and-join.md §Z-15: soft-deprecation signal.
        # /habits/{id}/join остаётся публичным endpoint'ом (для backward-compat
        # с тестами, admin-скриптами, и для уже-состоящих участников — реактивация
        # LEFT→ACTIVE идёт здесь, не через /subscribe). Но для НОВОГО вступления
        # правильный путь — POST /api/v1/payments/subscribe (там списывается
        # подписка + депозит одной транзакцией). Этот лог — диагностический
        # сигнал: если в проде увидим его в логах после того как Z-17 (frontend
        # refactor JoinButton) будет задеплоен, значит какой-то клиент всё ещё
        # вызывает /join для нового вступления. Не блокер, не warning — просто
        # событие для дашборда/алёртов.
        self._logger.info(
            "join_called_for_new_membership",
            extra={
                "user_id": user_id,
                "habit_id": habit_id,
                "note": "Use POST /api/v1/payments/subscribe for first-time join with payment",
            },
        )
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

    async def subscribe_and_join(
        self,
        *,
        user_id: int,
        habit_id: str,
        deposit_amount_kopecks: int,
        subscription_accepted: bool,
        idempotency_key: str,
    ) -> tuple[Membership, Transaction, bool]:
        """Единый платёж подписка+депозит + создание/реактивация ACTIVE membership.

        Pravki-subscribe-and-join.md §Z-13. Возвращает
        (membership, transaction, charged_subscription) — последний флаг показывает,
        списали ли price_month (True) или только депозит (False).

        Поток (атомарно в одной транзакции):
        1. Идемпотентность: SELECT Transaction WHERE idempotency_key == :key.
           Если найдена и параметры совпадают — вернуть (existing_m, existing_tx, existing_charged).
           Если найдена и параметры НЕ совпадают — 400 idempotency_conflict.
        2. habit = habit_repo.get(habit_id). None → 404. archived → 404. inactive → 409.
        3. existing = membership_repo.get_for_user_in_habit(user_id, habit_id).
           Разбираем 3 кейса (см. §Z-13.1 матрица и §Z-13.2 семантика):
           3a. existing is None ИЛИ (existing.status in (PAUSED, LEFT) И
               (existing.subscription_until is None ИЛИ existing.subscription_until < today)):
               → charged_subscription = True. Полная оплата.
               Если subscription_accepted == False → 422 subscription_required.
           3b. existing.status in (PAUSED, LEFT) И existing.subscription_until >= today:
               → charged_subscription = False. Подписка уже оплачена.
               subscription_accepted допустимо и True, и False.
           3c. existing.status == ACTIVE → 409 already_active.
        4. SELECT FOR UPDATE на user. None → UserNotFoundError (через InsufficientDepositError
           или новый — выбран путь raise ValueError для symmetry с PaymentService._apply).
        5. Валидация deposit_amount: должен быть >= habit.penalty_amount.
           Иначе 422 insufficient_deposit_choice.
        6. Применяем эффект на membership:
           - Кейс 3a (existing is None): создаём новую Membership
             (status=ACTIVE, joined_at=now, subscription_until=today+30d).
           - Кейс 3a (existing in PAUSED/LEFT): existing.status = ACTIVE,
             existing.subscription_until = today+30d. joined_at НЕ трогаем.
           - Кейс 3b (PAUSED/LEFT с активной подпиской):
             existing.status = ACTIVE. subscription_until и joined_at НЕ трогаем.
        7. u.deposit_balance += (итого списания):
           - Кейс 3a: += (habit.price_month + deposit_amount_kopecks).
           - Кейс 3b: += deposit_amount_kopecks (только депозит).
        8. Создаём Transaction:
           - Кейс 3a: type=SUBSCRIPTION, amount=price_month+deposit.
           - Кейс 3b: type=DEPOSIT_TOPUP, amount=deposit (см. §Z-13.3 — тип
             транзакции отражает что реально произошло, не "всё подписка").
        9. recompute_pause_status(user_id) — для всех клубов юзера.
        10. session.flush() — ловим IntegrityError на idempotency_key UNIQUE.
            При гонке возвращаем существующую транзакцию.
        11. Возвращаем (membership, transaction, charged_subscription).

        Не коммитит (commit на уровне handler'а, см. §Z-14).
        """
        full_key = f"subscribe:{idempotency_key}"

        # Шаг 1: идемпотентность.
        existing_tx_row = (
            await self._session.execute(
                select(Transaction).where(Transaction.idempotency_key == full_key)
            )
        ).scalar_one_or_none()
        if existing_tx_row is not None:
            # Проверяем, что related_membership_id соответствует нашему habit_id
            # (защита от reuse ключа с другим habit_id → 400 idempotency_conflict).
            if existing_tx_row.related_membership_id is None:
                raise IdempotencyConflictError()
            existing_m = await self._membership_repo.get(existing_tx_row.related_membership_id)
            if existing_m is None or existing_m.habit_id != habit_id:
                raise IdempotencyConflictError()
            # Идемпотентный retry: charged_subscription восстанавливаем из amount.
            # Если amount > deposit_amount_kopecks → была полная оплата, иначе только депозит.
            # Но надёжнее: пересчитать по membership.subscription_until.
            # Если subscription_until был установлен при первой транзакции (== today+30d или свежее),
            # значит была полная оплата. Иначе — только депозит (existing.subscription_until не трогали).
            # Для идемпотентности достаточно: если subscription_until == (initial_tx_date + 30d),
            # значит charged=True. Упрощение: смотрим на разницу amount vs deposit.
            charged_flag = existing_tx_row.amount > deposit_amount_kopecks
            self._logger.info(
                "subscribe_idempotent_retry",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "transaction_id": str(existing_tx_row.id),
                },
            )
            return existing_m, existing_tx_row, charged_flag

        # Шаг 2: получить habit, проверить archived/inactive.
        if self._habit_repo is None:
            raise RuntimeError(
                "MembershipService.subscribe_and_join requires habit_repo"
            )
        habit = await self._habit_repo.get(habit_id)
        if habit is None or habit.archived_at is not None:
            raise HabitNotFoundError()
        if not habit.is_active:
            raise HabitInactiveError()

        # Шаг 3: разобрать 3 кейса membership-состояния.
        existing = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        today = date.today()
        if existing is not None and existing.status == MembershipStatus.ACTIVE:
            raise AlreadyActiveError()

        has_active_subscription = (
            existing is not None
            and existing.status in (MembershipStatus.PAUSED, MembershipStatus.LEFT)
            and existing.subscription_until is not None
            and existing.subscription_until >= today
        )

        if has_active_subscription:
            # Кейс 3b: подписка активна, списываем только депозит.
            charged_subscription = False
        else:
            # Кейс 3a: новая membership или истёкшая/отсутствующая подписка.
            charged_subscription = True
            if not subscription_accepted:
                raise SubscriptionRequiredError()

        # Шаг 4: lock user.
        u = await self._user_repo.lock_for_update(user_id)
        if u is None:
            # Юзер не существует — крайне вырожденный кейс (race при удалении).
            raise ValueError(f"user {user_id} not found for subscribe_and_join")

        # Шаг 5: валидация deposit_amount.
        if deposit_amount_kopecks < habit.penalty_amount:
            self._logger.info(
                "subscribe_rejected_insufficient_choice",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "required_kopecks": habit.penalty_amount,
                    "chosen_kopecks": deposit_amount_kopecks,
                },
            )
            raise InsufficientDepositChoiceError(
                required_kopecks=habit.penalty_amount,
                chosen_kopecks=deposit_amount_kopecks,
            )

        # Шаг 6: применить эффект на membership.
        if existing is None:
            # Кейс 3a / новая membership.
            m = Membership(
                id=str(uuid4()),
                user_id=user_id,
                habit_id=habit_id,
                status=MembershipStatus.ACTIVE,
                subscription_until=today + timedelta(days=30),
            )
            self._session.add(m)
            await self._session.flush()
        else:
            # existing in (PAUSED, LEFT).
            existing.status = MembershipStatus.ACTIVE
            if charged_subscription:
                # Кейс 3a / переоплата подписки.
                existing.subscription_until = today + timedelta(days=30)
            # Кейс 3b: subscription_until НЕ трогаем.
            # joined_at в обоих случаях НЕ трогаем (см. §Z-13.2).
            m = existing
            await self._session.flush()

        # Шаг 7: посчитать total_charged (для tx.amount и alert в UI).
        # Subscription fee (price_month) идёт на «доход клуба/платформы»
        # (записывается в transactions как type=SUBSCRIPTION для аудита),
        # но НЕ попадает на deposit_balance — на нём лежат ТОЛЬКО деньги,
        # которые могут быть списаны как штраф.
        # Pravki-subscribe-and-join.md §Z-13 шаг 7: deposit_balance
        # пополняется на deposit_amount_kopecks, а не на price_month+deposit.
        # Это поймано пользователем при тестировании 2026-08-09:
        # «1000 это оплата клуба она не должна быть на депозите».
        if charged_subscription:
            total_charged = habit.price_month + deposit_amount_kopecks
        else:
            total_charged = deposit_amount_kopecks
        u.deposit_balance += deposit_amount_kopecks    # ← только депозитная часть

        # Шаг 8: создать транзакцию (тип условный — см. §Z-13.3).
        if charged_subscription:
            tx_type = TransactionType.SUBSCRIPTION.value
        else:
            tx_type = TransactionType.DEPOSIT_TOPUP.value
        tx = Transaction(
            id=str(uuid4()),
            user_id=user_id,
            type=tx_type,
            amount=total_charged,         # ← полная сумма списания с юзера (для UI/alert)
            balance_after=u.deposit_balance,    # ← только депозитная часть
            related_membership_id=m.id,
            idempotency_key=full_key,
        )
        self._session.add(tx)

        # Шаг 9: recompute пауз для всех клубов юзера (включая текущий).
        # u уже под FOR UPDATE, параллельные операции сериализуются.
        await self.recompute_pause_status(user_id)

        # Шаг 10: flush + обработка race на idempotency_key UNIQUE.
        try:
            await self._session.flush()
        except IntegrityError:
            # Параллельный POST с тем же ключом успел первым.
            # Откатываем нашу транзакцию и возвращаем существующую.
            await self._session.rollback()
            existing_tx_row = (
                await self._session.execute(
                    select(Transaction).where(Transaction.idempotency_key == full_key)
                )
            ).scalar_one_or_none()
            if existing_tx_row is None:
                # Крайне вырожденный кейс: кто-то удалил транзакцию между
                # нашим INSERT-fail и re-fetch. Нечего возвращать — поднимаем
                # ошибку явно, чтобы клиент увидел 500 (или handler превратил
                # в 409). Лучше явная ошибка чем silent success.
                self._logger.error(
                    "subscribe_idempotency_race_no_existing_tx",
                    extra={"user_id": user_id, "habit_id": habit_id, "key": full_key},
                )
                raise IdempotencyConflictError()
            # Защита от reuse ключа с другим habit_id (та же проверка что
            # в early-return на шаге 1). Если related_membership_id == None
            # или habit_id не совпадает — клиент использует один и тот же
            # idempotency_key для разных клубов, что явная ошибка клиента.
            if existing_tx_row.related_membership_id is None:
                self._logger.warning(
                    "subscribe_idempotency_race_orphan_tx",
                    extra={"user_id": user_id, "habit_id": habit_id, "key": full_key},
                )
                raise IdempotencyConflictError()
            existing_m = await self._membership_repo.get(existing_tx_row.related_membership_id)
            if existing_m is None or existing_m.habit_id != habit_id:
                self._logger.warning(
                    "subscribe_idempotency_race_habit_mismatch",
                    extra={
                        "user_id": user_id,
                        "requested_habit_id": habit_id,
                        "existing_habit_id": existing_m.habit_id if existing_m else None,
                        "key": full_key,
                    },
                )
                raise IdempotencyConflictError()
            charged_flag = existing_tx_row.amount > deposit_amount_kopecks
            self._logger.info(
                "subscribe_idempotent_race_resolved",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "transaction_id": str(existing_tx_row.id),
                },
            )
            return existing_m, existing_tx_row, charged_flag

        self._logger.info(
            "subscribe_and_join_done",
            extra={
                "user_id": user_id,
                "habit_id": habit_id,
                "membership_id": str(m.id),
                "charged_subscription": charged_subscription,
                "total_charged_kopecks": total_charged,
                "new_deposit_balance": u.deposit_balance,
                "subscription_until": m.subscription_until.isoformat() if m.subscription_until else None,
            },
        )
        return m, tx, charged_subscription

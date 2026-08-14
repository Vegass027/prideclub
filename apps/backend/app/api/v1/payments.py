"""User-facing payment endpoint (MVP).

Только mock-пополнение депозита через TelegramUserDep (initData).
Реальная интеграция с Telegram Payments будет через
`/internal/payments/confirm` (webhook от бота, см. internal_payments.py).

UI скрывает кнопку "+ Пополнить" если юзер не состоит ни в одном клубе,
но endpoint всё равно принимает любой habit_id — на MVP-мок это терпимо,
PaymentService.create membership под капотом (см. PaymentService._apply).
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.v1.users import TelegramUserDbDep
from app.core.deps import SessionDep
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository
from app.schemas import SubscribeRequest, SubscribeResponse
from app.services.membership_service import MembershipService
from app.services.payment_service import PaymentService

router = APIRouter()


class TopupRequest(BaseModel):
    """Pravki-deposit-sse.md §Z-2.5: депозит глобальный (на users.deposit_balance).

    `habit_id` опционален — раньше был обязательным для legacy-кода
    PaymentService._apply, который пытался создать membership при её
    отсутствии. После PR #1 membership-creation на topup больше не нужен:
    если user не имеет membership для (user_id, habit_id), транзакция
    просто записывается без `related_membership_id` (FK на memberships
    nullable). Это полностью закрывает §Z-2.5 «не принимает habit_id» —
    фронт может либо не слать поле вообще, либо слать `null`.

    Frontend PR #2 (apps/frontend/src/shared/hooks/index.ts:useTopUpDeposit)
    сейчас шлёт `{ habit_id: "", amount_kopecks }` — мы нормализуем
    пустую строку в None на уровне handler'а для backward-compat с
    таким клиентом (нормализация → None → skip membership lookup).
    """

    habit_id: str | None = None
    amount_kopecks: int = Field(gt=0, le=10_000_000)


class TopupResponse(BaseModel):
    ok: bool
    transaction_id: str | None = None
    new_deposit_balance: int | None = None
    code: str | None = None


@router.post("/payments/topup", response_model=TopupResponse)
async def topup_deposit(
    payload: TopupRequest,
    user: TelegramUserDbDep,
    session: SessionDep,
) -> TopupResponse:
    """Mock-пополнение депозита (MVP).

    Идемпотентность: charge_id = "mock:{uuid4()}" — каждый вызов уникальный,
    повторный POST создаст ещё одну транзакцию (это OK для мока).
    При подключении реального провайдера charge_id станет
    telegram_payment_charge_id, и UNIQUE-индекс на transactions.idempotency_key
    обеспечит идемпотентность webhook'ов.

    Безопасность:
    - TelegramUserDbDep: initData проверен middleware; user.id — авторитетный.
    - amount_kopecks: gt=0 (нельзя пополнить на 0 или минус), le=10M (cap 100k ₽).
    - PaymentService: SELECT FOR UPDATE на user (не membership — user-lock
      сериализует все catch/topup этого юзера в любых клубах, PR #1 §Z-2.4).
    """
    # Normalize empty-string → None (старые клиенты шлют habit_id=""; для
    # Pydantic это валидная непустая строка, но для бизнес-логики это "нет клуба").
    habit_id = payload.habit_id if payload.habit_id else None

    service = PaymentService(session)
    try:
        tx = await service.confirm_deposit_topup(
            charge_id=f"mock:{uuid4()}",
            user_id=user.id,
            habit_id=habit_id,
            amount_kopecks=payload.amount_kopecks,
        )
        await session.commit()
    except Exception:  # noqa: BLE001 — payment_failed — единый ответ UI
        await session.rollback()
        return TopupResponse(ok=False, code="payment_failed")

    return TopupResponse(
        ok=True,
        transaction_id=str(tx.id),
        new_deposit_balance=tx.balance_after,
    )


@router.post("/payments/subscribe", response_model=SubscribeResponse)
async def subscribe(
    payload: SubscribeRequest,
    user: TelegramUserDbDep,
    session: SessionDep,
) -> SubscribeResponse:
    """Единая оплата подписки+депозита с созданием ACTIVE membership.

    Pravki-subscribe-and-join.md §Z-14. MVP-мок: списываем price_month
    (если подписки ещё не было) + выбранную сумму депозита, кладём всё
    на users.deposit_balance, создаём или реактивируем membership до ACTIVE.

    Идемпотентность: client-supplied `idempotency_key` (uuid4 из фронта),
    префикс «subscribe:» добавляется в сервисе (изоляция namespace от
    confirm_deposit_topup). UNIQUE constraint на transactions.idempotency_key
    + повторный SELECT в except-блоке гарантируют safe-retry.

    Безопасность:
    - TelegramUserDbDep: initData проверен middleware; user.id — авторитетный.
    - subscription_accepted: server-side gate (см. §Z-13.1 матрица).
    - deposit_amount_kopecks: gt=0 (Pydantic), le=10M (cap 100k ₽).
    - idempotency_key: min_length=8, max_length=128 (защита от пустых/огромных строк).
    - MembershipService.subscribe_and_join: SELECT FOR UPDATE на user
      сериализует параллельные subscribe/topup этого юзера.

    Ошибки (все через глобальный handler в main.py:129-142):
    - 404 habit_not_found: клуб не существует или archived.
    - 409 already_active: membership уже ACTIVE.
    - 409 habit_inactive: клуб is_active=False.
    - 422 insufficient_deposit_choice: chosen deposit < penalty клуба.
    - 422 subscription_required: нет подписки + subscription_accepted=False.
    - 400 idempotency_conflict: тот же ключ с другим habit_id (или без membership).
    - 500 internal_error: всё прочее (через глобальный Exception handler).

    Rate limit: автоматически через middleware (`/api/v1/*` → 60/60s
    на юзера, см. core/constants.py:HttpRateLimitConfig.RATE_LIMIT_API_V1).

    Commit — после успешного возврата из сервиса. На любом исключении —
    rollback и re-raise (глобальный handler форматирует JSON).
    """
    service = MembershipService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
        user_repo=UserRepository(session),
    )

    try:
        membership, transaction, charged_subscription = await service.subscribe_and_join(
            user_id=user.id,
            habit_id=payload.habit_id,
            deposit_amount_kopecks=payload.deposit_amount_kopecks,
            subscription_accepted=payload.subscription_accepted,
            idempotency_key=payload.idempotency_key,
        )
    except Exception:
        # Любое исключение (DomainError или неожиданное) → откатываем
        # транзакцию и пробрасываем наверх. Глобальный handler в main.py
        # (DomainError → JSON со status_code + code; Exception → 500 JSON)
        # сам отформатирует HTTP-ответ. Дублировать логирование здесь
        # не нужно — Exception handler в main.py:153 уже пишет в лог.
        await session.rollback()
        raise

    # Commit только после успешного возврата из сервиса (правило
    # layered architecture: одна транзакция = один handler).
    await session.commit()

    return SubscribeResponse(
        ok=True,
        transaction_id=str(transaction.id),
        membership_id=str(membership.id),
        # balance_after: инвариант — MembershipService.subscribe_and_join
        # всегда устанавливает Transaction.balance_after = u.deposit_balance
        # (User.deposit_balance: int NOT NULL DEFAULT 0, см. user.py:34).
        # Если в БД окажется NULL (баг в коде создания транзакции), Pydantic v2
        # выбросит ValidationError → 500 через глобальный handler. Это лучше
        # чем silent fallback на 0: пользователь увидит ошибку, а не «неверный
        # баланс в кошельке». Не пишем `or 0` чтобы не маскировать регрессию.
        new_deposit_balance=transaction.balance_after,
        # subscription_until: гарантированно установлен во всех 3 кейсах
        # (см. §Z-13 шаг 6 в MembershipService.subscribe_and_join). Если
        # почему-то None — Pydantic v2 выбросит ValidationError при сериализации,
        # что лучше silent fallback'а.
        subscription_until=membership.subscription_until,
        # Pravki §Z-13.3 fix: transaction — это dep_tx, amount = только депозит.
        # total_charged_kopecks = полная сумма списания с юзера для alert в UI
        # (price_month + deposit если подписка платная, иначе только deposit).
        total_charged_kopecks=(
            habit.price_month + transaction.amount
            if charged_subscription
            else transaction.amount
        ),
        charged_subscription=charged_subscription,
    )

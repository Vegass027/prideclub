from __future__ import annotations

import json
from typing import Any

from aiogram import F, Router
from aiogram.types import Message

from bot.config import get_settings
from bot.logging_setup import get_logger


router = Router(name="payments")
log = get_logger("bot.payments")


@router.pre_checkout_query()
async def process_pre_checkout_query(query) -> None:  # type: ignore[no-untyped-def]
    """Telegram-бот обязан ответить на pre_checkout_query в течение 10 сек."""
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message) -> None:
    """Telegram присылает сообщение об успешной оплате.

    payload содержит charge_id, kind, habit_id, months (если был правильный Extra).
    """
    settings = get_settings()
    sp = message.successful_payment
    if sp is None:
        return

    try:
        payload_obj: dict[str, Any] = json.loads(sp.invoice_payload or "{}")
    except json.JSONDecodeError:
        log.warning("payment_bad_payload", extra={"payload": sp.invoice_payload})
        return

    kind = payload_obj.get("kind", "subscription")
    months = int(payload_obj.get("months", 1))
    habit_id = payload_obj.get("habit_id", "")
    user_id = message.from_user.id if message.from_user else 0
    amount = int(sp.total_amount)  # уже в копейках

    import aiohttp

    from security import generate_service_token

    token = generate_service_token(
        service_name="bot",
        target_audience="backend-api",
        secret=settings.service_secret,
        ttl_seconds=60,
    )
    headers = {"X-Service-Token": token, "Content-Type": "application/json"}
    body = {
        "charge_id": sp.telegram_payment_charge_id,
        "user_id": user_id,
        "habit_id": habit_id,
        "amount_kopecks": amount,
        "kind": kind,
        "months": months,
    }

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        try:
            async with session.post(
                f"{settings.backend_url}/internal/payments/confirm",
                json=body,
                headers=headers,
            ) as resp:
                result = await resp.json()
                log.info(
                    "payment_confirmed",
                    extra={
                        "kind": kind,
                        "code": result.get("code"),
                        "tx_id": result.get("transaction_id"),
                    },
                )
        except aiohttp.ClientError as exc:
            log.error("payment_backend_unreachable", extra={"err": str(exc)})

    await message.answer("Оплата получена. Спасибо!")
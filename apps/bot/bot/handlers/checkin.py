from datetime import datetime, timezone
from typing import Any

import aiohttp
from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.config import get_settings
from bot.logging_setup import get_logger


router = Router(name="checkin")
log = get_logger("bot.checkin")


def _parse_proof(message: Message) -> dict[str, Any] | None:
    """aiogram Message → Backend payload."""
    sent_at = (message.date or datetime.now(tz=timezone.utc)).astimezone(timezone.utc)
    base = {
        "chat_id": message.chat.id,
        "message_thread_id": getattr(message, "message_thread_id", None),
        "message_id": message.message_id,
        "message_sent_at": sent_at.isoformat(),
    }
    if message.video_note:
        return {
            **base,
            "proof_type": "video_note",
            "duration_seconds": message.video_note.duration or 0,
        }
    if message.photo:
        return {**base, "proof_type": "photo"}
    if message.text:
        return {**base, "proof_type": "text", "text": message.text}
    return None


async def _send_to_backend(payload: dict[str, Any]) -> dict[str, Any]:
    from security import generate_service_token

    settings = get_settings()
    token = generate_service_token(
        service_name="bot",
        target_audience="backend-api",
        secret=settings.service_secret,
        ttl_seconds=60,
    )
    headers = {"X-Service-Token": token, "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.post(
            f"{settings.backend_url}/internal/checkins/process",
            json=payload,
            headers=headers,
        ) as resp:
            return await resp.json()


@router.message(F.video_note | F.photo | F.text)
async def handle_proof(message: Message, bot: Bot) -> None:
    if message.chat.id == message.from_user.id:
        return  # личка

    proof = _parse_proof(message)
    if proof is None:
        return

    payload = {
        "user_id": message.from_user.id,
        **proof,
    }

    try:
        result = await _send_to_backend(payload)
        code = result.get("code", "unknown")
        if code not in ("ok", "checkin_already_exists"):
            log.warning("checkin_rejected", extra={"code": code})
            return
        log.info("checkin_accepted", extra={"user_id": message.from_user.id})
    except aiohttp.ClientError as exc:
        # backend/redis/network недоступен — Celery-таска не поставлена.
        # НЕ логируем checkin_accepted: пользователю придёт ошибка из Mini App
        # при следующем опросе, а здесь только фиксируем инцидент для алертов.
        log.error(
            "checkin_dispatch_failed",
            extra={"err": str(exc), "kind": "network"},
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "checkin_dispatch_failed",
            extra={"err": str(exc), "kind": "unexpected"},
        )
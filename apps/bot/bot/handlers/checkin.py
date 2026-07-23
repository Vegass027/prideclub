from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.handlers import checkin_texts
from bot.logging_setup import get_logger
from bot.services.api_client import BackendClient

router = Router(name="checkin")
log = get_logger("bot.checkin")


# Коды успеха (запрос ушёл в Celery). На оба шлём одинаковое подтверждение.
_SUCCESS_CODES: frozenset[str] = frozenset({"ok", "checkin_already_exists"})

# Коды, при которых сообщение пользователю можно слать (не чужой чат).
_SILENT_CODES: frozenset[str] = frozenset({"habit_not_found"})


def _parse_proof(message: Message) -> dict[str, Any] | None:
    """aiogram Message → Backend payload."""
    sent_at = (message.date or datetime.now(tz=UTC)).astimezone(UTC)
    base: dict[str, Any] = {
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


def _text_for_code(code: str | None) -> str:
    """Маппинг backend/worker code → пользовательский текст."""
    if code in ("forwarded",):
        return checkin_texts.REJECT_FORWARDED
    if code in ("too_short",):
        return checkin_texts.REJECT_TOO_SHORT
    if code in ("wrong_topic", "checkin_wrong_topic"):
        return checkin_texts.REJECT_WRONG_TOPIC
    if code in ("out_of_window", "checkin_window_closed"):
        return checkin_texts.REJECT_OUT_OF_WINDOW
    return checkin_texts.REJECT_UNKNOWN


async def _reply(
    bot: Bot,
    message: Message,
    text: str,
) -> None:
    """Шлёт ответ в ту же тему (топик) форума.

    Без reply_to_message_id: в топиках-форумах Telegram не разрешает боту
    (даже админу) reply'ить на чужие сообщения — `Bad Request: message to
    be replied not found`. Поэтому просто шлём в ту же message_thread_id.
    """
    await bot.send_message(
        chat_id=message.chat.id,
        message_thread_id=getattr(message, "message_thread_id", None),
        text=text,
    )


@router.message(F.video_note | F.photo | F.text)
async def handle_proof(
    message: Message,
    bot: Bot,
    backend: BackendClient,
) -> None:
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
        result = await backend.post("/internal/checkins/process", payload)
    except Exception as exc:  # noqa: BLE001 — network / unexpected, не валим хэндлер
        log.error(
            "checkin_dispatch_failed",
            extra={"err": str(exc), "kind": exc.__class__.__name__},
        )
        await _reply(bot, message, checkin_texts.NETWORK_FAIL)
        return

    # Контракт: {"ok": bool, "task_id": str|None, "code": str|None}.
    # На успехе ok=True, code=None; на отказе ok=False, code="…".
    ok = result.get("ok") is True
    code = result.get("code")

    if ok or code in _SUCCESS_CODES:
        log.info(
            "checkin_accepted",
            extra={
                "user_id": message.from_user.id,
                "task_id": result.get("task_id"),
            },
        )
        await _reply(bot, message, checkin_texts.ACCEPTED_OK)
        return

    if code in _SILENT_CODES:
        log.warning("checkin_rejected_silent", extra={"code": code})
        return

    log.warning("checkin_rejected", extra={"code": code})
    await _reply(bot, message, _text_for_code(code))
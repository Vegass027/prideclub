"""Фабрика фейковых Telegram Update JSON для POST /bot/webhook.

Возвращает Python dict, сериализуется в JSON при HTTP. Структура
повторяет реальный Telegram Update envelope — aiogram 3.x парсит
через pydantic Update.model_validate({"update_id": int, "message": {...}}).

Покрывает типичные сценарии:
- /start command
- video_note в топик (check-in для habit с proof_type=video_note)
- text в топик (чек-ин text)
- photo в топик
- forwarded вариант (для антифрод-сценариев)

chat_id/user_id — параметры. `message_id` и `update_id` — небольшие
положительные int (<=2^31-1), потому что в БД `checkins.proof_message_id`
имеет тип INTEGER (32-bit). Используем синтетический счётчик, чтобы
были уникальны в рамках сценария. По умолчанию `_msg_counter`
инкрементируется — вызывающий может передать свой `message_id`.

`message_thread_id` =None для legacy-клубов (без топиков); integer
для topic-scoped.
"""
from __future__ import annotations

import itertools
import time
from typing import Any

from scripts.e2e.auth import FakeUser


# Счётчик message_id — глобальный для процесса, чтобы несколько вызовов
# в одном сценарии получали уникальные message_id (иначе duplicate-key
# в checkins.proof_message_id). Сценарий может и сам передавать
# message_id явно (например для repro конкретного теста).
_msg_counter = itertools.count(start=100_000)


def _next_message_id() -> int:
    return next(_msg_counter)


def _next_update_id() -> int:
    return next(_msg_counter)


def _chat_block(chat_id: int) -> dict[str, Any]:
    return {
        "id": chat_id,
        "type": "supergroup",
        "title": f"e2e-chat-{chat_id}",
    }


def _from_block(user: FakeUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "is_bot": False,
        "first_name": user.first_name,
        "username": user.username,
        "language_code": user.language_code or "ru",
    }


def _video_note_object(
    *,
    duration_seconds: int,
    length: int = 240,  # 240×240 px — канонический square для кружка
) -> dict[str, Any]:
    """Telegram VideoNote object: только обязательные поля для парсинга."""
    fid = f"e2e-vn-{_next_message_id()}"
    return {
        "file_id": fid,
        "file_unique_id": fid,
        "length": length,
        "duration": duration_seconds,
    }


def _photo_object() -> dict[str, Any]:
    """Photo: массив sizes, берём 1 фото размера 90×90 (минимум для парсинга)."""
    return [
        {
            "file_id": "e2e-photo-small",
            "file_unique_id": "e2e-photo-small-uid",
            "width": 90,
            "height": 90,
            "file_size": 1000,
        }
    ]


def make_start_update(
    *,
    chat_id: int,
    user: FakeUser,
    message_id: int | None = None,
    message_thread_id: int | None = None,
) -> dict[str, Any]:
    """Update с /start (CommandStart). Возвращает dict, готовый к JSON."""
    text = "/start"
    return {
        "update_id": _next_update_id(),
        "message": {
            "message_id": message_id if message_id is not None else _next_message_id(),
            "date": int(time.time()),
            "chat": _chat_block(chat_id),
            "from": _from_block(user),
            "text": text,
            "entities": [
                {"type": "bot_command", "offset": 0, "length": len(text)},
            ],
            "message_thread_id": message_thread_id,
            "is_topic_message": message_thread_id is not None,
        },
    }


def make_video_note_update(
    *,
    chat_id: int,
    user: FakeUser,
    duration_seconds: int = 4,
    message_id: int | None = None,
    message_thread_id: int | None = None,
    is_forwarded: bool = False,
) -> dict[str, Any]:
    """Update с video_note для приёма чек-ина.

    `duration_seconds` — длина кружка. <3 отвергается антифродом.
    `is_forwarded=True` добавляет forward_date (антифрод-check #4).
    """
    msg: dict[str, Any] = {
        "message_id": message_id if message_id is not None else _next_message_id(),
        "date": int(time.time()),
        "chat": _chat_block(chat_id),
        "from": _from_block(user),
        "video_note": _video_note_object(duration_seconds=duration_seconds),
        "message_thread_id": message_thread_id,
        "is_topic_message": message_thread_id is not None,
    }
    if is_forwarded:
        msg["forward_date"] = int(time.time()) - 3600
        msg["forward_from_chat"] = _chat_block(-1000000000999)
        msg["forward_from_message_id"] = 42
    return {
        "update_id": _next_update_id(),
        "message": msg,
    }


def make_text_update(
    *,
    chat_id: int,
    user: FakeUser,
    text: str = "чек-ин готов",
    message_id: int | None = None,
    message_thread_id: int | None = None,
    is_forwarded: bool = False,
) -> dict[str, Any]:
    """Update с text для приёма чек-ина (habit с proof_type включает 'text')."""
    msg: dict[str, Any] = {
        "message_id": message_id if message_id is not None else _next_message_id(),
        "date": int(time.time()),
        "chat": _chat_block(chat_id),
        "from": _from_block(user),
        "text": text,
        "message_thread_id": message_thread_id,
        "is_topic_message": message_thread_id is not None,
    }
    if is_forwarded:
        msg["forward_date"] = int(time.time()) - 3600
    return {
        "update_id": _next_update_id(),
        "message": msg,
    }


def make_photo_update(
    *,
    chat_id: int,
    user: FakeUser,
    message_id: int | None = None,
    message_thread_id: int | None = None,
    is_forwarded: bool = False,
) -> dict[str, Any]:
    """Update с photo для приёма чек-ина."""
    msg: dict[str, Any] = {
        "message_id": message_id if message_id is not None else _next_message_id(),
        "date": int(time.time()),
        "chat": _chat_block(chat_id),
        "from": _from_block(user),
        "photo": _photo_object(),
        "message_thread_id": message_thread_id,
        "is_topic_message": message_thread_id is not None,
    }
    if is_forwarded:
        msg["forward_date"] = int(time.time()) - 3600
    return {
        "update_id": _next_update_id(),
        "message": msg,
    }

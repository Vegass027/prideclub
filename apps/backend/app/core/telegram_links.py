"""Парсер ссылок на топики Telegram (forum supergroups).

Единственный поддерживаемый формат — приватные супергруппы:
    https://t.me/c/<chat_id>/<thread_id>

Публичные ссылки (t.me/<username>/<msg_id>) для топиков не работают —
там не передаётся thread_id.

Используется в HabitService при создании/обновлении клуба для
извлечения (chat_id, thread_id) и сохранения в Habit.checkin_topic_thread_id
/ Habit.notifications_topic_thread_id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.exceptions import InvalidTopicLinkError

_TOPIC_LINK_RE = re.compile(
    r"^https?://t\.me/c/(?P<chat_id>-?\d+)/(?P<thread_id>\d+)/?$"
)


@dataclass(frozen=True)
class TelegramTopic:
    """chat_id + thread_id топика форума."""

    chat_id: int
    thread_id: int


def parse_telegram_topic_link(url: str) -> TelegramTopic:
    """Парсит ссылку на топик Telegram.

    Возвращает TelegramTopic с положительными chat_id (для супергрупп)
    и thread_id (>0).

    Бросает InvalidTopicLinkError при невалидном формате.
    """
    if not isinstance(url, str) or not url:
        raise InvalidTopicLinkError("Ссылка на топик не указана")

    match = _TOPIC_LINK_RE.match(url.strip())
    if not match:
        raise InvalidTopicLinkError(
            "Ссылка должна быть в формате https://t.me/c/<chat_id>/<thread_id>"
        )

    chat_id = int(match.group("chat_id"))
    thread_id = int(match.group("thread_id"))

    if chat_id == 0:
        raise InvalidTopicLinkError("chat_id не может быть 0")
    if thread_id <= 0:
        raise InvalidTopicLinkError("thread_id должен быть положительным")

    return TelegramTopic(chat_id=chat_id, thread_id=thread_id)


def make_topic_link(chat_id: int, thread_id: int) -> str:
    """Собирает ссылку на топик из chat_id и thread_id (для UI)."""
    return f"https://t.me/c/{chat_id}/{thread_id}"
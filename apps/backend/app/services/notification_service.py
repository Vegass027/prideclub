"""NotificationService — публикация событий ловли и штрафов в топик уведомлений.

Использует Telegram Bot API напрямую через aiohttp (как и другие места в
backend, см. apps/backend/app/api/admin/v1/habits.py:_verify_chats_via_telegram).

Дизайн:
- Принимает `bot_token` через конструктор (DI), не читает env напрямую.
- Принимает `api_base_url` через конструктор — для тестов.
- Никогда не бросает наружу: ошибки Telegram API логируются, бизнес-флоу
  продолжается. Штраф — это финансовая транзакция, она уже в БД; уведомление
  в чат — best-effort.

Контракт публикации:
- Если у клуба `notifications_topic_thread_id IS NULL` — уведомление
  публикуется в General чата (`chat_id`, без `thread_id`).
- Если задан — в указанный топик.

PII:
- Не логируем `first_name`/`username`. В логе только `user_id`/`habit_id`.
"""
from __future__ import annotations

from typing import Protocol

import aiohttp

from app.core.logging import get_logger
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.user import User


class UserLookupPort(Protocol):
    """Порт для получения first_name по user_id (для текста уведомления)."""

    async def get_by_id(self, user_id: int) -> User | None: ...


class NotificationService:
    def __init__(
        self,
        bot_token: str,
        *,
        api_base_url: str = "https://api.telegram.org",
        user_lookup: UserLookupPort | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._api_base_url = api_base_url.rstrip("/")
        self._user_lookup = user_lookup
        self._logger = get_logger("notification_service")

    async def notify_catch(
        self,
        *,
        habit: Habit,
        catcher_membership: Membership | None,
        catcher_user: User | None,
        violator_membership: Membership,
        violator_user: User | None,
        penalty_amount_kopecks: int,
    ) -> bool:
        """Публикует событие поимки в топик уведомлений.

        Возвращает True при успехе, False при любой ошибке (бизнес не падает).
        """
        text = self._format_catch_text(
            catcher_first_name=(catcher_user.first_name if catcher_user else None),
            violator_first_name=(
                violator_user.first_name if violator_user else None
            ),
            penalty_amount_kopecks=penalty_amount_kopecks,
        )
        return await self._send(
            chat_id=habit.chat_id,
            message_thread_id=habit.notifications_topic_thread_id,
            text=text,
            habit_id=str(habit.id),
        )

    async def notify_window_closed(
        self,
        *,
        habit: Habit,
        violator_membership: Membership,
        violator_user: User | None,
        penalty_amount_kopecks: int,
    ) -> bool:
        text = self._format_window_closed_text(
            violator_first_name=(
                violator_user.first_name if violator_user else None
            ),
            penalty_amount_kopecks=penalty_amount_kopecks,
        )
        return await self._send(
            chat_id=habit.chat_id,
            message_thread_id=habit.notifications_topic_thread_id,
            text=text,
            habit_id=str(habit.id),
        )

    async def _send(
        self,
        *,
        chat_id: int,
        message_thread_id: int | None,
        text: str,
        habit_id: str,
    ) -> bool:
        if not self._bot_token:
            self._logger.warning(
                "notification_skip_no_bot_token",
                extra={"habit_id": habit_id, "chat_id": chat_id},
            )
            return False
        if chat_id == 0:
            self._logger.info(
                "notification_skip_no_chat",
                extra={"habit_id": habit_id},
            )
            return False

        url = f"{self._api_base_url}/bot{self._bot_token}/sendMessage"
        params: dict[str, str | int] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if message_thread_id is not None:
            params["message_thread_id"] = message_thread_id

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
            ) as session:
                async with session.post(url, params=params) as resp:
                    data = await resp.json(content_type=None)
            if not data.get("ok"):
                description = data.get("description", "telegram_error")
                self._logger.warning(
                    "notification_send_failed",
                    extra={
                        "habit_id": habit_id,
                        "chat_id": chat_id,
                        "thread_id": message_thread_id,
                        "err": description,
                    },
                )
                return False
        except (TimeoutError, aiohttp.ClientError) as exc:
            self._logger.warning(
                "notification_send_network_error",
                extra={
                    "habit_id": habit_id,
                    "chat_id": chat_id,
                    "thread_id": message_thread_id,
                    "err": str(exc),
                },
            )
            return False

        self._logger.info(
            "notification_sent",
            extra={
                "habit_id": habit_id,
                "chat_id": chat_id,
                "thread_id": message_thread_id,
            },
        )
        return True

    @staticmethod
    def _format_catch_text(
        *,
        catcher_first_name: str | None,
        violator_first_name: str | None,
        penalty_amount_kopecks: int,
    ) -> str:
        catcher = _safe_first_name(catcher_first_name)
        violator = _safe_first_name(violator_first_name)
        rubles = penalty_amount_kopecks // 100
        return (
            f"👨🏽‍🦰 {catcher} словил(а) 👨🏽‍🦰 {violator}\n"
            f"💸 {rubles} ₽ ушли в призовой фонд клуба"
        )

    @staticmethod
    def _format_window_closed_text(
        *,
        violator_first_name: str | None,
        penalty_amount_kopecks: int,
    ) -> str:
        violator = _safe_first_name(violator_first_name)
        rubles = penalty_amount_kopecks // 100
        return (
            f"⏰ Окно чек-ина закрыто\n"
            f"👨🏽‍🦰 {violator} не отметился(ась)\n"
            f"💸 {rubles} ₽ ушли в призовой фонд клуба"
        )


def _safe_first_name(name: str | None) -> str:
    """Возвращает имя или анонимную заглушку; в логи не уходит."""
    if isinstance(name, str):
        cleaned = name.strip()
        if cleaned:
            return cleaned
    return "Аноним"
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message


class RateLimitMiddleware(BaseMiddleware):
    """Заглушка для шага 3. Шаг 4 — реальный rate-limit на 'Спалить'."""

    async def __call__(self, handler, event: Message, data: dict[str, Any]) -> Any:
        return await handler(event, data)
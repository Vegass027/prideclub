"""AvatarService — proxy Telegram CDN URL для фото профиля пользователя.

Подход C' (см. Pravki.md §7.1):
    - Backend НЕ хранит фото (нет disk I/O, нет S3, нет volume).
    - Backend НЕ стримит файл через StreamingResponse (нет bandwidth).
    - В БД хранится только photo_file_id (постоянный между вызовами).
    - На каждый запрос endpoint вызывает bot.getFile(file_id),
      кэширует file_path в Redis на 6 часов и возвращает 307 redirect
      на Telegram CDN с полным URL (включая токен бота).

Безопасность:
    - Endpoint требует initData (TelegramUserDbDep) — без него 401.
    - Токен бота НЕ попадает в JSON клиента (URL приходит через 307 Location).
    - Rate-limit на /api/v1/* уже стоит (60/min/user в middleware).
    - 6ч кэш достаточно: Telegram CDN TTL 1ч + запас 6x на случай моргания.

Failure modes:
    - Telegram Bot API недоступен → return None (fallback на инициалы в UI).
    - Фото у юзера удалено в Telegram → photo_file_id остаётся, но getFile
      вернёт {"ok": false}. Обрабатываем как None.
    - Redis недоступен → работаем без кэша (каждый запрос = 1 call to Bot API).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import aiohttp
import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


class AvatarService:
    """Получает Telegram CDN URL для фото профиля пользователя.

    DI через конструктор: bot_token + Redis + aiohttp.ClientSession
    передаются явно (см. AGENTS.md — "DI через конструктор").
    """

    REDIS_TTL_SECONDS = 6 * 60 * 60  # 6 часов (Telegram CDN TTL = 1ч)
    REQUEST_TIMEOUT_SECONDS = 5

    def __init__(
        self,
        *,
        bot_token: str,
        redis: Redis,
        http_factory: Callable[[], aiohttp.ClientSession],
    ) -> None:
        self._bot_token = bot_token
        self._redis = redis
        # http_factory (callable) вместо готового ClientSession: создаём
        # сессию внутри async-метода, где event loop уже привязан.
        # Иначе RuntimeError "no running event loop" в sync DI.
        self._http_factory = http_factory

    async def get_cdn_url(self, user_id: int, file_id: str | None) -> str | None:
        """Возвращает полный Telegram CDN URL или None если нет данных / ошибка.

        Args:
            user_id: Telegram user_id (числовой, используется только для Redis-ключа).
            file_id: Telegram file_id (постоянный, из `users.photo_file_id`).

        Returns:
            URL вида "https://api.telegram.org/file/bot<TOKEN>/photos/file_12.jpg"
            или None если file_id пустой / Bot API вернул ошибку / timeout.
        """
        if not file_id:
            return None

        cached = await self._get_cached(user_id)
        if cached:
            return self._build_cdn_url(cached)

        file_path = await self._fetch_file_path(file_id)
        if not file_path:
            return None

        await self._cache(user_id, file_path)
        return self._build_cdn_url(file_path)

    def _redis_key(self, user_id: int) -> str:
        return f"user_photo:{user_id}"

    async def _get_cached(self, user_id: int) -> str | None:
        try:
            value = await self._redis.get(self._redis_key(user_id))
        except Exception as exc:  # noqa: BLE001 — Redis fallback: работаем без кэша
            logger.warning(
                "avatar_redis_get_failed",
                extra={"user_id": user_id, "err": str(exc)},
            )
            return None
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    async def _cache(self, user_id: int, file_path: str) -> None:
        try:
            await self._redis.setex(
                self._redis_key(user_id),
                self.REDIS_TTL_SECONDS,
                file_path,
            )
        except Exception as exc:  # noqa: BLE001 — Redis fallback: кэш опционален
            logger.warning(
                "avatar_redis_setex_failed",
                extra={"user_id": user_id, "err": str(exc)},
            )

    async def _fetch_file_path(self, file_id: str) -> str | None:
        """bot.getFile → {file_path, file_size} → возвращает file_path."""
        url = f"https://api.telegram.org/bot{self._bot_token}/getFile"
        try:
            # Создаём ClientSession внутри async-метода, где event loop
            # уже привязан. Закрываем по выходу — нет утечки.
            async with self._http_factory() as session:
                async with session.get(
                    url,
                    params={"file_id": file_id},
                    timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    data: dict[str, Any] = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as exc:
            logger.warning(
                "avatar_get_file_network_error",
                extra={"file_id_prefix": file_id[:16], "err": str(exc)},
            )
            return None

        if not data.get("ok"):
            description = data.get("description", "telegram_error")
            logger.warning(
                "avatar_get_file_telegram_error",
                extra={"file_id_prefix": file_id[:16], "description": description},
            )
            return None

        result = data.get("result") or {}
        return result.get("file_path")

    def _build_cdn_url(self, file_path: str) -> str:
        return f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}"


__all__ = ["AvatarService"]

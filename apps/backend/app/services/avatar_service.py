"""AvatarService — локальный кеш фото профиля пользователя.

Подход D (см. Pravki.md §7.1 v3 — 2026-07-24):
    - Backend скачивает JPEG с Telegram CDN ОДИН раз через bot.getFile +
      GET к api.telegram.org/file/bot<TOKEN>/<file_path>, сохраняет на диск
      в `<STATIC_DIR>/avatars/{user_id}.jpg`.
    - Nginx отдаёт файл напрямую через try_files, минуя FastAPI — нулевая
      нагрузка на backend для каждого просмотра лидерборда.
    - Если файла нет (новый юзер до update_user_photos cron), endpoint
      скачивает синхронно и сохраняет (следующий запрос — мгновенный hit).
    - В БД хранится photo_file_id. Redis кеширует file_id → file_path
      на 6ч для инвалидации при смене фото (file_id меняется в Telegram).

Безопасность:
    - Endpoint требует initData (TelegramUserDep) — без него 401.
    - Токен бота НЕ попадает в клиент (URL в Network tab = /api/v1/users/.../photo).
    - File path = f"{user_id}.jpg" (только числовой user_id) — нельзя
      проинъектировать ../../.
    - Rate-limit 60/min/user на /api/v1/* (middleware).

Почему не 307 redirect (Pravki.md §7.1 v2 — отказались 2026-07-24):
    - Telegram CDN отдаёт `Content-Type: application/octet-stream` +
      `Content-Disposition: attachment`. Браузер скачивает вместо того
      чтобы рендерить в <img> (RFC 6266, behavior independent of CSP).
    - Токен бота утекает в URL `api.telegram.org/file/bot<TOKEN>/...`
      → виден в DevTools любому пользователю.

Failure modes:
    - Telegram Bot API недоступен → return None (fallback на инициалы в UI).
    - Фото у юзера удалено в Telegram → file_id есть, getFile → {"ok": false}.
    - Redis недоступен → работаем без кеша file_id (проверка по mtime файла).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import aiohttp
import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


class AvatarService:
    """Локальный кеш аватарок пользователей + Telegram fallback.

    DI через конструктор: bot_token + Redis + aiohttp.ClientSession +
    avatars_dir (Pydantic Settings STATIC_DIR/avatars) — явно.
    """

    REDIS_TTL_SECONDS = 6 * 60 * 60  # 6 часов (Telegram CDN TTL = 1ч)
    REQUEST_TIMEOUT_SECONDS = 5
    # Max JPEG size: 5 МБ. Telegram profile photos ≤ 10 МБ обычно, но
    # ограничиваем 5 МБ как защиту от аномальных ответов.
    MAX_JPEG_BYTES = 5 * 1024 * 1024

    def __init__(
        self,
        *,
        bot_token: str,
        redis: Redis,
        http: aiohttp.ClientSession,
        avatars_dir: Path | str,
    ) -> None:
        self._bot_token = bot_token
        self._redis = redis
        self._http = http
        self._avatars_dir = Path(avatars_dir)

    @property
    def avatars_dir(self) -> Path:
        """Директория хранения JPEG-кэша. Создаётся в lifespan."""
        return self._avatars_dir

    def _path_for(self, user_id: int) -> Path:
        """Path к локальному файлу: <avatars_dir>/<user_id>.jpg.

        user_id — int из БД (Telegram user_id). Path не принимает
        user input, поэтому path-traversal невозможен.
        """
        return self._avatars_dir / f"{user_id}.jpg"

    async def get_or_fetch_local_path(
        self, user_id: int, file_id: str | None
    ) -> Path | None:
        """Возвращает Path к локальному JPEG, скачивая с Telegram при необходимости.

        Args:
            user_id: Telegram user_id (числовой, для имени файла и Redis-ключа).
            file_id: Telegram file_id (из `users.photo_file_id`).

        Returns:
            Path к JPEG-файлу или None если file_id пустой / Telegram недоступен /
            file удалён в Telegram.
        """
        if not file_id:
            return None

        local_path = self._path_for(user_id)
        cached_file_id = await self._get_cached_file_id(user_id)

        # Hot path: файл есть И file_id совпадает → отдаём без обращения к TG.
        if cached_file_id == file_id and local_path.exists():
            return local_path

        # Cold path / file changed: скачиваем заново.
        jpeg_bytes = await self._download_jpeg(file_id)
        if jpeg_bytes is None:
            return None

        # Сохраняем на диск атомарно: write в .tmp, потом rename.
        # Если два запроса параллельно — последний rename выиграет, оба
        # будут валидным JPEG. Не критично.
        await self._write_atomic(local_path, jpeg_bytes)
        await self._cache_file_id(user_id, file_id)
        return local_path

    async def get_cdn_url(self, user_id: int, file_id: str | None) -> str | None:
        """Возвращает полный Telegram CDN URL или None.

        Сохранён для тестов и fallback-сценариев (когда локальный кеш
        не создан, но нужен URL — например, для background-задач
        которые скачивают аватарки напрямую через worker).
        """
        if not file_id:
            return None
        cached = await self._get_cached_file_path(user_id)
        if cached:
            return self._build_cdn_url(cached)
        file_path = await self._fetch_file_path(file_id)
        if not file_path:
            return None
        return self._build_cdn_url(file_path)

    # --- внутренние методы ---

    def _redis_key_file_id(self, user_id: int) -> str:
        return f"user_photo_file_id:{user_id}"

    def _redis_key_file_path(self, user_id: int) -> str:
        return f"user_photo:{user_id}"

    async def _get_cached_file_id(self, user_id: int) -> str | None:
        try:
            value = await self._redis.get(self._redis_key_file_id(user_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("avatar_redis_get_failed", extra={"user_id": user_id, "err": str(exc)})
            return None
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def _cache_file_id(self, user_id: int, file_id: str) -> None:
        try:
            await self._redis.setex(
                self._redis_key_file_id(user_id), self.REDIS_TTL_SECONDS, file_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("avatar_redis_setex_failed", extra={"user_id": user_id, "err": str(exc)})

    async def _get_cached_file_path(self, user_id: int) -> str | None:
        """Старый кеш file_path — для get_cdn_url. Можно удалить через 6ч TTL."""
        try:
            value = await self._redis.get(self._redis_key_file_path(user_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("avatar_redis_get_failed", extra={"user_id": user_id, "err": str(exc)})
            return None
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def _fetch_file_path(self, file_id: str) -> str | None:
        """bot.getFile → {file_path, file_size} → возвращает file_path."""
        url = f"https://api.telegram.org/bot{self._bot_token}/getFile"
        try:
            async with self._http.get(
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
        return (data.get("result") or {}).get("file_path")

    def _build_cdn_url(self, file_path: str) -> str:
        return f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}"

    async def _download_jpeg(self, file_id: str) -> bytes | None:
        """Скачивает JPEG: getFile → file_path → GET cdn_url → bytes."""
        file_path = await self._fetch_file_path(file_id)
        if not file_path:
            return None
        cdn_url = self._build_cdn_url(file_path)
        try:
            async with self._http.get(
                cdn_url,
                timeout=aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        "avatar_download_non_200",
                        extra={"file_id_prefix": file_id[:16], "status": resp.status},
                    )
                    return None
                content = await resp.content.read(self.MAX_JPEG_BYTES + 1)
                if len(content) > self.MAX_JPEG_BYTES:
                    logger.warning(
                        "avatar_download_too_large",
                        extra={"file_id_prefix": file_id[:16], "size": len(content)},
                    )
                    return None
                return content
        except (TimeoutError, aiohttp.ClientError) as exc:
            logger.warning(
                "avatar_download_network_error",
                extra={"file_id_prefix": file_id[:16], "err": str(exc)},
            )
            return None

    async def _write_atomic(self, path: Path, content: bytes) -> None:
        """Записывает JPEG атомарно: write → fsync → rename.

        path.parent — директория, должна существовать (lifespan создаёт).
        rename атомарен на одной FS (ext4 внутри контейнера).
        """
        def _sync_write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with open(tmp_path, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            tmp_path.replace(path)

        # sync file I/O в thread (не блокируем event loop)
        await asyncio.to_thread(_sync_write)


__all__ = ["AvatarService"]

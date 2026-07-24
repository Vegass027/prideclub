"""Singleton aiohttp.ClientSession для исходящих запросов к Telegram Bot API.

Используется в:
    - notification_service.py (sendMessage к чатам клубов)
    - avatar_service.py (getFile для фото профиля)
    - worker update_user_photos (getUserProfilePhotos)

Lifecycle:
    Session создаётся в FastAPI lifespan (см. main.py:_lifespan) и
    закрывается на shutdown. Worker создаёт свою сессию в task setup
    (см. apps/worker/worker/tasks/_http_session.py если появится).

Почему singleton:
    aiohttp.ClientSession поддерживает connection pool (TCP keep-alive).
    Каждый новый ClientSession = новый TLS handshake на каждый запрос.
    На 1000 users в лидерборде это 1000 handshake = ~30 сек. С singleton
    — только первый запрос.
"""
from __future__ import annotations

import aiohttp

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        )
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


__all__ = ["get_session", "close_session"]

"""HTTP client для исходящих запросов к Telegram Bot API.

Используется в:
    - notification_service.py (sendMessage к чатам клубов)
    - avatar_service.py (getFile для фото профиля)
    - worker update_user_photos (getUserProfilePhotos)

Lifecycle:
    ClientSession создаётся в FastAPI lifespan (см. main.py:_lifespan)
    и кладётся в app.state.bot_http. На shutdown закрывается.

Почему singleton через app.state:
    aiohttp.ClientSession поддерживает connection pool (TCP keep-alive).
    Каждый новый ClientSession = новый TLS handshake на каждый запрос.
    На 1000 users в лидерборде это 1000 handshake = ~30 сек. С singleton
    — только первый запрос.

    Сессия привязана к event loop (uvicorn worker). Создавать её в DI
    нельзя — DI выполняется в threadpool без running loop (Pravki.md §7.1
    bug fix 2026-07-24: `RuntimeError: no running event loop`).
"""
from __future__ import annotations

import aiohttp

# Timeout — 10 сек. Telegram Bot API обычно отвечает <1с, но бывают
# задержки при cold start сессии. 10с — запас для retry.
_TIMEOUT = aiohttp.ClientTimeout(total=10)


def make_session() -> aiohttp.ClientSession:
    """Создаёт новую ClientSession. Вызывать ТОЛЬКО из async-контекста
    (lifespan, worker setup, async-метод сервиса). DI вызовет RuntimeError.
    """
    return aiohttp.ClientSession(timeout=_TIMEOUT)


async def close_session(session: aiohttp.ClientSession) -> None:
    if session is not None and not session.closed:
        await session.close()


__all__ = ["make_session", "close_session"]

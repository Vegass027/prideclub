"""HTTP client для исходящих запросов к Telegram Bot API.

Используется в:
    - notification_service.py (sendMessage к чатам клубов)
    - avatar_service.py (getFile для фото профиля)
    - worker update_user_photos (getUserProfilePhotos)

Почему НЕ singleton:
    aiohttp.ClientSession привязан к event loop. В FastAPI handler
    `get_session()` вызывается из DI (sync), и если там лежит
    singleton session, созданный в _lifespan'е, то при вызове из
    worker-threadpool (Depends get_avatar_service → get_bot_http →
    get_session) — событийный цикл ещё не привязан → RuntimeError:
    "no running event loop".

Решение: TCPConnector(limit=20) внутри каждого ClientSession.
    aiohttp сам переиспользует TCP-соединения внутри сессии (keep-alive),
    а сессия живёт один handler — этого достаточно для нашего
    масштаба (1-2 запроса в handler).

Для prod-сценария с миллионами req/мин — нужно FastAPI lifespan
с одним loop. Сейчас не наш случай.
"""
from __future__ import annotations

import aiohttp


def get_session() -> aiohttp.ClientSession:
    """Создаёт НОВЫЙ ClientSession на каждый вызов (handler-scoped).

    Caller обязан закрыть через `await session.close()` или через
    `async with session:` (используется в AvatarService._fetch_file_path).

    Без явного `connector=` — aiohttp создаст default TCPConnector
    внутри ClientSession.__init__, когда event loop уже привязан
    (через asyncio.get_event_loop()). Явный TCPConnector требует
    event loop в момент создания → RuntimeError если вызвано из
    sync DI (Depends).
    """
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))


__all__ = ["get_session"]


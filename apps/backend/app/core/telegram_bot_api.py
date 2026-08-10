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


# === Backward-compat алиасы для main.py + deps.py (post-stash cleanup) ===
# Stash@{0} обновил 4 файла (deps.py, telegram_bot_api.py, avatar_service.py,
# test_avatar_service.py) но НЕ обновил main.py который импортирует
# make_session/close_session из telegram_bot_api. А deps.py ожидает
# get_bot_http как callable. Алиасы сохраняют обе API до отдельного PR
# на рефакторинг main.py (убрать singleton session из lifespan, раз
# новая модель handler-scoped).

async def close_session(session: aiohttp.ClientSession) -> None:
    """Закрывает переданную сессию (lifespan shutdown).

    Идемпотентно — если сессия уже закрыта, не валится.
    """
    if session is not None and not session.closed:
        await session.close()


# make_session / get_bot_http — алиасы для единой handler-scoped фабрики.
# main.py использует make_session() в lifespan (хотя теперь это просто
# создаёт новую сессию без глобального состояния — caller сам хранит).
make_session = get_session
get_bot_http = get_session


__all__ = ["get_session", "close_session", "make_session", "get_bot_http"]


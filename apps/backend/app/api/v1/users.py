from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse

from app.core.deps import AvatarServiceDep, SessionDep
from app.core.security import TelegramUser
from app.models.user import User
from app.repositories.user_repository import UserRepository


def current_user(request: Request) -> TelegramUser:
    """Возвращает TelegramUser из request.state (после AuthMiddleware).

    Только достаёт — не делает запросов в БД. Используйте current_user_db
    если нужно автоматически создать/обновить запись пользователя.
    """
    user: TelegramUser | None = getattr(request.state, "telegram_user", None)
    if user is None:
        from app.core.exceptions import MissingInitDataError

        raise MissingInitDataError()
    return user


async def current_user_db(
    user: Annotated[TelegramUser, Depends(current_user)],
    session: SessionDep,
) -> TelegramUser:
    """Возвращает TelegramUser + upsert'ит запись в `users`.

    Вызывайте в эндпоинтах где пользователь гарантированно должен
    существовать в БД (marketplace, today, members, balance, leaderboard,
    join, leave, catch, payments). Это решает FK violation при первом
    обращении нового юзера.

    Коммитим сразу — пользователь должен существовать ДО того как мы
    начнём работать с FK на него. Соглашение "1 handler = 1 транзакция"
    сохраняется: внутри endpoint'а будет ещё одна транзакция (через
    session.begin() после session.close()).
    """
    repo = UserRepository(session)
    await repo.upsert(
        id=user.id,
        first_name=user.first_name or "User",
        username=user.username,
    )
    await session.commit()
    return user


def current_user_internal(request: Request) -> str:
    """Маркер, что запрос прошёл через service-token middleware.

    Возвращает имя сервиса-caller'а. Для /internal/* роутов.
    """
    caller: str | None = getattr(request.state, "caller", None)
    if caller is None:
        from app.core.exceptions import MissingServiceTokenError

        raise MissingServiceTokenError()
    return caller


from fastapi import APIRouter  # noqa: E402

router = APIRouter()


TelegramUserDep = Annotated[TelegramUser, Depends(current_user)]
TelegramUserDbDep = Annotated[TelegramUser, Depends(current_user_db)]
ServiceCallerDep = Annotated[str, Depends(current_user_internal)]


@router.get("/me")
async def me(user: TelegramUserDep) -> dict:
    return {
        "id": user.id,
        "first_name": user.first_name,
        "username": user.username,
        "language_code": user.language_code,
        "is_premium": user.is_premium,
    }


@router.get("/users/{user_id}/photo")
async def get_user_photo(
    user_id: int,
    caller: TelegramUserDep,
    session: SessionDep,
    avatar: AvatarServiceDep,
) -> RedirectResponse:
    """307 redirect на Telegram CDN с аватаркой пользователя.

    Подход C' (см. Pravki.md §7.1):
    - Токен бота остаётся server-side (URL приходит через 307 Location,
      не в JSON клиента).
    - Файл не хранится у нас — каждый запрос получает file_path через
      bot.getFile (кэш в Redis на 6ч, см. AvatarService.REDIS_TTL_SECONDS).

    Безопасность:
    - TelegramUserDep требует initData (AuthMiddleware → 401 без него).
      Это не даёт перебирать user_id анонимам и видеть кто в каких
      клубах состоит (photo_file_id привязан к user).
    - HTTP rate-limit 60/min/user стоит на /api/v1/* (см.
      services.http_rate_limiter.make_api_v1_limiter).

    Failure modes:
    - Telegram Bot API timeout/error → 502 telegram_unavailable.
      Клиент показывает инициалы (Avatar fallback).
    - photo_file_id отсутствует → 404 photo_unavailable.
    """
    target = await session.get(User, user_id)
    if target is None or not target.photo_file_id:
        from app.core.exceptions import PhotoUnavailableError

        raise PhotoUnavailableError()

    cdn_url = await avatar.get_cdn_url(target.id, target.photo_file_id)
    if cdn_url is None:
        from app.core.exceptions import TelegramUnavailableError

        raise TelegramUnavailableError()

    return RedirectResponse(cdn_url, status_code=307)

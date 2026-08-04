from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import FileResponse

from app.core.deps import AvatarServiceDep, SessionDep
from app.core.exceptions import PhotoUnavailableError
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
) -> FileResponse:
    """Отдаёт JPEG-аватарку из локального кеша (Pravki.md §7.1 v3, подход D).

    Архитектура:
    - AvatarService скачивает JPEG с Telegram CDN ОДИН раз и сохраняет
      в `<STATIC_DIR>/avatars/{user_id}.jpg`.
    - nginx может отдавать файл напрямую через try_files (минуя FastAPI),
      но этот endpoint работает как fallback при кеш-промахе.
    - Параллельно с этим endpoint фронт получает photo_url как
      `/api/v1/users/{id}/photo` (relative), который абсолютно через
      `new URL(row.photo_url, window.location.origin)`.

    Безопасность:
    - TelegramUserDep → 401 без initData (анонимный перебор user_id невозможен).
    - HTTP rate-limit 60/min/user (middleware).
    - Имя файла = f"{user_id}.jpg" — числовой user_id, никаких ../.
    - Токен бота НЕ утекает в URL — клиент видит только /api/v1/users/.../photo.

    Failure modes:
    - photo_file_id отсутствует → 404 photo_unavailable.
    - Telegram CDN timeout/error → 404 photo_unavailable (UI: инициалы).
    - Redis недоступен → file всё равно отдаётся (проверка по mtime).
    """
    target = await session.get(User, user_id)
    if target is None or not target.photo_file_id:
        raise PhotoUnavailableError()

    local_path = await avatar.get_or_fetch_local_path(target.id, target.photo_file_id)
    if local_path is None:
        raise PhotoUnavailableError()

    return FileResponse(
        local_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=21600",  # 6ч, синхронно с Redis TTL
        },
    )

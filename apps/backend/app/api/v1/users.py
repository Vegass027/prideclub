from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.core.deps import SessionDep
from app.core.security import TelegramUser
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

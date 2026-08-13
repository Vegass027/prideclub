from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.deps import AvatarServiceDep, SessionDep
from app.core.exceptions import PhotoUnavailableError
from app.core.security import TelegramUser
from app.models.habit import Habit
from app.models.membership import Membership, MembershipStatus
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas import WalletClubOut, WalletOut


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


@router.get("/me/wallet", response_model=WalletOut)
async def me_wallet(
    user: TelegramUserDbDep,
    session: SessionDep,
) -> WalletOut:
    """Pravki-deposit-sse.md §Z-4.1: глобальный депозит + активные клубы с can_checkin.

    Используется на фронте для:
    - блокировки кнопки «Открыть клуб» на Today page, если deposit < penalty;
    - предзаполнения TopUpModal при ошибке join'а (insufficient_deposit);
    - отображения баланса в Profile.

    Один SQL JOIN на (memberships × habits) — для клубов нужен только
    penalty_amount (для can_checkin) + title (для UI) + status (для badge'а).
    Текущий deposit берётся с уже поднятого в TelegramUserDbDep user'а.

    `can_checkin` дублирует результат MembershipService.recompute_pause_status
    (user.deposit_balance >= habit.penalty_amount). LEFT-membership'ы
    исключены — `status != LEFT`.
    """
    user_row = await session.get(User, user.id)
    if user_row is None:
        # TelegramUserDbDep уже сделал upsert, но защищаемся от race.
        return WalletOut(deposit_balance=0, active_clubs=[])

    rows = (
        await session.execute(
            select(Membership, Habit.penalty_amount, Habit.title)
            .join(Habit, Habit.id == Membership.habit_id)
            .where(
                Membership.user_id == user.id,
                Membership.status != MembershipStatus.LEFT,
            )
        )
    ).all()

    active_clubs = [
        WalletClubOut(
            habit_id=str(m.habit_id),
            title=title,
            penalty_amount=int(penalty_amount),
            can_checkin=user_row.deposit_balance >= int(penalty_amount),
            status=m.status.value,
            # Pravki-subscribe-and-join.md §Z-17 substep 1: передаём из JOIN.
            # None если юзер ещё ни разу не платил подписку (или membership
            # только что создана через legacy /join, см. §Z-13.2 семантика).
            subscription_until=m.subscription_until,
        )
        for m, penalty_amount, title in rows
    ]

    return WalletOut(
        deposit_balance=user_row.deposit_balance,
        active_clubs=active_clubs,
    )


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

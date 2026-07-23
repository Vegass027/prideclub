"""Admin /admin/v1/habits — owner-only управление клубами (TZ §3.6).

Эндпоинты:
- POST   /admin/v1/habits                 — создать клуб (is_active=false)
- GET    /admin/v1/habits                 — список всех клубов (включая архив)
- GET    /admin/v1/habits/{id}            — детали клуба
- PATCH  /admin/v1/habits/{id}            — частичное обновление
- POST   /admin/v1/habits/{id}/activate   — тумблер is_active
- POST   /admin/v1/habits/{id}/archive    — soft-delete
- POST   /admin/v1/habits/{id}/restore    — снять архив (is_active остаётся false)

Owner-gate происходит на уровне AuthMiddleware (request.state.telegram_user).
Здесь только маршрутизация + DI сервиса.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Annotated

import aiohttp
from fastapi import APIRouter, Depends, status

from app.api.v1.internal_bot import (
    AVAILABLE_CHATS_KEY,
    _drop_stale_records,
    _record_available_chat,
)
from app.api.v1.users import TelegramUserDep
from app.core.deps import SessionDep
from app.core.logging import get_logger
from app.db.redis import get_redis
from app.models.habit import Habit
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.schemas import (
    AdminHabitActionResponse,
    AdminHabitAvailableChat,
    AdminHabitAvailableChatsResponse,
    AdminHabitChatStatusResponse,
    AdminHabitCreateRequest,
    AdminHabitForceFinancialsRequest,
    AdminHabitForceFinancialsResponse,
    AdminHabitOut,
    AdminHabitPreviewChatRequest,
    AdminHabitPreviewChatResponse,
    AdminHabitRefreshChatResponse,
    AdminHabitsListResponse,
    AdminHabitToggleRequest,
    AdminHabitUpdateRequest,
)
from app.services.habit_service import HabitService

logger = get_logger("admin.preview_chat")


_TR_INVITE_RE = re.compile(r"^(?:https?://)?t\.me/(?:\+([A-Za-z0-9_-]+)|([A-Za-z0-9_]+))$")


router = APIRouter()


def _get_habit_service(
    session: SessionDep,
) -> HabitService:
    return HabitService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
    )


HabitServiceDep = Annotated[HabitService, Depends(_get_habit_service)]


def _habit_to_out(habit: Habit, active_members_count: int = 0) -> AdminHabitOut:
    from app.core.telegram_links import make_topic_link

    checkin_topic_link = (
        make_topic_link(habit.chat_id, habit.checkin_topic_thread_id)
        if habit.checkin_topic_thread_id is not None
        and habit.chat_id != 0
        else None
    )
    notifications_topic_link = (
        make_topic_link(habit.chat_id, habit.notifications_topic_thread_id)
        if habit.notifications_topic_thread_id is not None
        and habit.chat_id != 0
        else None
    )
    chat_topic_link = (
        make_topic_link(habit.chat_id, habit.chat_topic_thread_id)
        if habit.chat_topic_thread_id is not None
        and habit.chat_id != 0
        else None
    )
    return AdminHabitOut(
        id=str(habit.id),
        title=habit.title,
        description=habit.description,
        chat_id=habit.chat_id,
        checkin_window_start=habit.checkin_window_start.isoformat(),
        checkin_window_end=habit.checkin_window_end.isoformat(),
        timezone=habit.timezone,
        penalty_amount=habit.penalty_amount,
        price_month=habit.price_month,
        proof_type=habit.proof_type.value,
        proof_types=list(habit.proof_types or []),
        prize_pool=habit.prize_pool,
        is_active=habit.is_active,
        photo_url=habit.photo_url,
        telegram_invite_link=habit.telegram_invite_link,
        stat_name=habit.stat_name,
        stat_icon=habit.stat_icon,
        stat_gain_per_checkin=habit.stat_gain_per_checkin,
        stat_loss_per_miss=habit.stat_loss_per_miss,
        member_limit=habit.member_limit,
        curator_id=habit.curator_id,
        checkin_topic_thread_id=habit.checkin_topic_thread_id,
        notifications_topic_thread_id=habit.notifications_topic_thread_id,
        chat_topic_thread_id=habit.chat_topic_thread_id,
        checkin_topic_link=checkin_topic_link,
        notifications_topic_link=notifications_topic_link,
        chat_topic_link=chat_topic_link,
        archived_at=habit.archived_at,
        created_at=habit.created_at,
        active_members_count=active_members_count,
    )


@router.post(
    "/habits",
    response_model=AdminHabitOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_habit(
    payload: AdminHabitCreateRequest,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitOut:
    """Создать клуб. Всегда is_active=false (TZ §3.6.4)."""
    habit = await service.create(
        admin_id=user.id,
        title=payload.title,
        description=payload.description,
        photo_url=payload.photo_url,
        telegram_invite_link=payload.telegram_invite_link,
        stat_name=payload.stat_name,
        stat_icon=payload.stat_icon,
        chat_id=payload.chat_id,
        checkin_window_start=payload.checkin_window_start,
        checkin_window_end=payload.checkin_window_end,
        timezone_str=payload.timezone,
        proof_types=payload.proof_types or [],
        price_month=payload.price_month,
        penalty_amount=payload.penalty_amount,
        stat_gain_per_checkin=payload.stat_gain_per_checkin,
        stat_loss_per_miss=payload.stat_loss_per_miss,
        member_limit=payload.member_limit,
        curator_id=payload.curator_id,
        checkin_topic_link=payload.checkin_topic_link,
        notifications_topic_link=payload.notifications_topic_link,
        chat_topic_link=payload.chat_topic_link,
    )
    await service._session.commit()  # noqa: SLF001 — admin endpoint, commit разрешён
    return _habit_to_out(habit)


@router.get("/habits", response_model=AdminHabitsListResponse)
async def list_habits(
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitsListResponse:
    """Все клубы (включая архивированные)."""
    repo = service._habit_repo  # noqa: SLF001
    items = await repo.list_including_archived()
    out: list[AdminHabitOut] = []
    for h in items:
        active_count = await repo.count_active_members(str(h.id))
        out.append(_habit_to_out(h, active_members_count=active_count))
    return AdminHabitsListResponse(items=out)


# Bot API: статусы, в которых бот НЕ состоит в чате.
_BOT_NOT_IN_CHAT_STATUSES = frozenset({"left", "kicked"})

_BOT_ID_CACHE_KEY = "bot:bot_id_cache"


async def _get_bot_id(bot_token: str) -> int | None:
    """Возвращает Telegram id бота, кэшируя в Redis (TTL 24ч)."""
    redis = get_redis()
    cached = await redis.get(_BOT_ID_CACHE_KEY)
    if cached:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5),
        ) as session:
            async with session.get(
                f"https://api.telegram.org/bot{bot_token}/getMe"
            ) as resp:
                data = await resp.json(content_type=None)
        if data.get("ok") and data.get("result", {}).get("id"):
            bot_id = int(data["result"]["id"])
            await redis.set(_BOT_ID_CACHE_KEY, str(bot_id), ex=86400)
            return bot_id
    except (TimeoutError, aiohttp.ClientError):
        pass
    return None


async def _verify_chats_via_telegram(
    bot_token: str,
    chat_ids: list[int],
    bot_user_id: int | None,
) -> dict[int, dict | None]:
    """Для каждого chat_id дёргает getChatMember и возвращает {chat_id: result}.

    Возвращает dict: ключ — chat_id, значение:
      - dict с полями status/title/type/invite_link если бот в группе,
      - None если бот удалён/вышел/kicked (запись надо удалить из Redis),
      - dict {"_error": "..."} если Telegram API вернул ошибку (например
        chat not found — группу удалили полностью).
    Параллелим запросы через asyncio.gather для скорости.
    """
    api_url = f"https://api.telegram.org/bot{bot_token}/getChatMember"

    async def _one(chat_id: int) -> tuple[int, dict | None]:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
            ) as session:
                async with session.get(
                    api_url,
                    params={"chat_id": chat_id, "user_id": bot_user_id},
                ) as resp:
                    payload = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as exc:
            return chat_id, {"_error": str(exc)}

        if not payload.get("ok"):
            desc = payload.get("description", "telegram_error")
            params = payload.get("parameters") or {}
            # Миграция группы в супергруппу: chat_id изменился.
            # Старый chat_id больше не существует — помечаем для удаления
            # из Redis. Новый chat_id сохраняем отдельно, чтобы админка
            # могла подхватить его автоматически.
            if "migrate_to_chat_id" in params:
                return chat_id, {
                    "_error": desc,
                    "_migrated_to": int(params["migrate_to_chat_id"]),
                }
            # Бот кикнут / чат удалён — это окончательно, не временный сбой.
            # Удаляем из Redis.
            desc_lower = desc.lower()
            is_bot_kicked = (
                "bot was kicked" in desc_lower
                or "bot is not a member" in desc_lower
                or "chat not found" in desc_lower
                or "group chat was deactivated" in desc_lower
            )
            if is_bot_kicked:
                return chat_id, {"_bot_left": True, "_error": desc}
            return chat_id, {"_error": desc}

        result = payload.get("result", {})
        status = result.get("status")
        return chat_id, {
            "status": status,
            "_bot_left": status in _BOT_NOT_IN_CHAT_STATUSES,
        }

    pairs = await asyncio.gather(*[_one(c) for c in chat_ids])
    return dict(pairs)


@router.get(
    "/habits/available_chats",
    response_model=AdminHabitAvailableChatsResponse,
)
async def list_available_chats(
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitAvailableChatsResponse:
    """Список Telegram-чатов, куда @join_prideclub_bot добавлен.

    Источник — Redis ZSET `bot:available_chats`, но при каждом запросе
    делает ЖИВУЮ проверку через Telegram getChatMember:
      - если бот всё ещё в группе — обновляет title/type в Redis и возвращает;
      - если бот удалён/вышел (status: left/kicked) — запись из Redis удаляется,
        и этот чат НЕ показывается в админке.

    Это гарантирует, что админ видит только актуальные группы, куда бот
    реально добавлен, даже если webhook my_chat_member не пришёл
    (например, бот был offline в момент удаления).

    ОБЪЯВЛЕН ДО /habits/{habit_id} — иначе FastAPI ловит
    'available_chats' как habit_id и пытается загрузить из БД.
    """
    from app.api.v1.internal_bot import _record_available_chat
    from app.core.config import get_settings

    settings = get_settings()
    bot_token = settings.bot_token

    redis = get_redis()
    raw_items = await redis.zrevrange(AVAILABLE_CHATS_KEY, 0, 199, withscores=True)

    # Собираем уникальные chat_id для проверки
    seen: set[int] = set()
    by_chat_id: dict[int, dict] = {}
    by_chat_id_score: dict[int, float] = {}
    for raw, score in raw_items:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        chat_id_raw = data.get("chat_id")
        if not isinstance(chat_id_raw, int) or chat_id_raw in seen:
            continue
        seen.add(chat_id_raw)
        by_chat_id[chat_id_raw] = data
        by_chat_id_score[chat_id_raw] = float(score)

    # Живая проверка Telegram — какие группы ещё актуальны
    bot_user_id = await _get_bot_id(bot_token) if bot_token else None
    if bot_token and by_chat_id and bot_user_id:
        try:
            verifications = await _verify_chats_via_telegram(
                bot_token=bot_token,
                chat_ids=list(by_chat_id.keys()),
                bot_user_id=bot_user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("available_chats.verify_failed", extra={"err": str(exc)})
            verifications = {}
    else:
        verifications = {}

    # Reconcile БД: проверяем ВСЕ habits.chat_id != 0 против Telegram.
    # Если при привязке бот был удалён из группы без webhook'а my_chat_member
    # (или группа просто разогнана), `habits.chat_id` остаётся в БД указывая
    # на мёртвый chat. Обнуляем до 0, чтобы админка не показывала фейковый
    # «бот в группе», пока Redis-список уже вычищен.
    repo_all_chats = await service._habit_repo.list_chat_ids_for_reconcile()  # noqa: SLF001
    stale_cleared = 0
    if bot_token and bot_user_id and repo_all_chats:
        db_check_chat_ids = [
            cid for cid in repo_all_chats if cid not in verifications
        ]
        if db_check_chat_ids:
            try:
                db_verifications = await _verify_chats_via_telegram(
                    bot_token=bot_token,
                    chat_ids=db_check_chat_ids,
                    bot_user_id=bot_user_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "available_chats.db_verify_failed",
                    extra={"err": str(exc)},
                )
                db_verifications = {}
        else:
            db_verifications = {}
        for cid, info in db_verifications.items():
            if not info.get("_bot_left"):
                continue
            habit = await service._habit_repo.get_by_chat_id(cid)  # noqa: SLF001
            if habit is None:
                continue
            habit.chat_id = 0
            stale_cleared += 1
            logger.info(
                "available_chats.habit_chat_unbound",
                extra={"habit_id": str(habit.id), "chat_id": cid},
            )
        if stale_cleared:
            await service._session.commit()  # noqa: SLF001 — admin endpoint
            logger.info(
                "available_chats.habits_cleared",
                extra={"count": stale_cleared},
            )

    items: list[AdminHabitAvailableChat] = []
    repo = service._habit_repo  # noqa: SLF001

    for chat_id, cached in by_chat_id.items():
        verification = verifications.get(chat_id)

        # Бот удалён из группы — выкидываем из Redis и не показываем
        if verification and verification.get("_bot_left"):
            await _drop_stale_records(redis, chat_id)
            logger.info(
                "available_chats.bot_left_dropped",
                extra={"chat_id": chat_id},
            )
            continue

        # Группа мигрировала в супергруппу — старый chat_id больше не
        # существует. Удаляем из Redis и подменяем на новый.
        if verification and "_migrated_to" in verification:
            new_chat_id = verification["_migrated_to"]
            await _drop_stale_records(redis, chat_id)
            # Записываем новый chat_id как доступный чат
            await _record_available_chat(
                chat_id=new_chat_id,
                chat_title=cached.get("chat_title"),
                chat_type=cached.get("chat_type"),
                invite_link=cached.get("invite_link"),
                actor_user_id=None,
            )
            logger.info(
                "available_chats.migrated",
                extra={
                    "old_chat_id": chat_id,
                    "new_chat_id": new_chat_id,
                },
            )
            continue

        # Telegram вернул ошибку (chat not found, network) — НЕ удаляем
        # запись (может быть временный сбой), показываем как есть
        if verification and "_error" in verification:
            logger.warning(
                "available_chats.verify_error_kept",
                extra={
                    "chat_id": chat_id,
                    "err": verification["_error"],
                },
            )

        # Если у Telegram получилось дёрнуть getChatMember, обновим
        # title/type в Redis (отдельно не дёргаем getChat — экономим
        # API quota, берём из cached и доверяем Telegram кэшу).
        # Если нужен свежий title — кнопка «Обновить» использует refresh_chat.

        bound_habit = await repo.get_by_chat_id(chat_id)  # noqa: SLF001
        items.append(
            AdminHabitAvailableChat(
                chat_id=chat_id,
                chat_title=cached.get("chat_title"),
                chat_type=cached.get("chat_type"),
                invite_link=cached.get("invite_link"),
                added_at=by_chat_id_score[chat_id],
                bound_to_habit_id=(
                    str(bound_habit.id) if bound_habit is not None else None
                ),
                bound_to_habit_title=(
                    bound_habit.title if bound_habit is not None else None
                ),
            )
        )

    return AdminHabitAvailableChatsResponse(items=items)


@router.post(
    "/habits/refresh_chat/{chat_id}",
    response_model=AdminHabitRefreshChatResponse,
)
async def refresh_chat(
    chat_id: int,
    user: TelegramUserDep,
) -> AdminHabitRefreshChatResponse:
    """Принудительно обновить название/тип чата из Telegram.

    Используется кнопкой «Обновить» в форме создания клуба, когда админ
    переименовал группу в Telegram и хочет увидеть актуальные данные
    без повторного добавления бота. Дёргает Telegram getChat по числовому
    chat_id (он уже есть в ZSET после my_chat_member, так что бот точно
    имеет к нему доступ) и обновляет Redis.

    Эндпоинт объявлен ДО /habits/{habit_id}, чтобы chat_id не ушёл в
    habit_id-парсер (там ожидается UUID).
    """
    from app.core.config import get_settings

    settings = get_settings()
    bot_token = settings.bot_token
    if not bot_token:
        return AdminHabitRefreshChatResponse(
            ok=False,
            chat_id=chat_id,
            code="bot_token_missing",
            message="BOT_TOKEN не настроен",
        )

    api_url = f"https://api.telegram.org/bot{bot_token}/getChat"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        ) as session:
            async with session.get(api_url, params={"chat_id": chat_id}) as resp:
                data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError) as exc:
        logger.warning("refresh_chat.http_failed", extra={"err": str(exc)})
        return AdminHabitRefreshChatResponse(
            ok=False,
            chat_id=chat_id,
            code="telegram_unreachable",
            message="Не удалось связаться с Telegram",
        )

    if not data.get("ok"):
        description = data.get("description", "Telegram вернул ошибку")
        logger.info(
            "refresh_chat.telegram_error",
            extra={"chat_id": chat_id, "description": description},
        )
        return AdminHabitRefreshChatResponse(
            ok=False,
            chat_id=chat_id,
            code="telegram_error",
            message=description,
        )

    result = data.get("result", {})
    chat_title = result.get("title") or result.get("username") or None
    chat_type = result.get("type")
    invite_link = result.get("invite_link")

    await _record_available_chat(
        chat_id=chat_id,
        chat_title=chat_title,
        chat_type=chat_type,
        invite_link=invite_link,
        actor_user_id=None,
    )

    return AdminHabitRefreshChatResponse(
        ok=True,
        chat_id=chat_id,
        chat_title=chat_title,
        chat_type=chat_type,
        invite_link=invite_link,
    )




def _drop_chat_from_redis(chat_id: int) -> int:
    """Удалить все записи в ZSET по chat_id."""
    redis = get_redis()
    raw_items = redis.zrange(AVAILABLE_CHATS_KEY, 0, -1)
    to_delete: list[str] = []
    for raw in raw_items:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("chat_id") == chat_id:
            to_delete.append(raw)
    if to_delete:
        redis.zrem(AVAILABLE_CHATS_KEY, *to_delete)
    return len(to_delete)


@router.post("/habits/dismiss_chat/{chat_id}")
async def dismiss_chat(
    chat_id: int,
    user: TelegramUserDep,
) -> dict:
    """Вручную убрать чат из списка доступных групп.

    Используется когда бот был удалён из группы, но Redis-кэш ещё не
    обновился (например, my_chat_member не пришёл). Удаляет все записи
    с этим chat_id из ZSET `bot:available_chats`. К самой Telegram-группе
    никак не относится.
    """
    from app.core.logging import get_logger
    log = get_logger("admin.dismiss_chat")
    removed = await _drop_chat_from_redis(chat_id)
    log.info(
        "dismiss_chat",
        extra={"chat_id": chat_id, "removed": removed, "user_id": user.id},
    )
    return {"ok": True, "chat_id": chat_id, "removed_records": removed}


@router.get("/habits/{habit_id}", response_model=AdminHabitOut)
async def get_habit(
    habit_id: str,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitOut:
    """Детали клуба (включая архив)."""
    habit = await service._habit_repo.get(habit_id)  # noqa: SLF001
    if habit is None:
        from app.core.exceptions import HabitNotFoundError

        raise HabitNotFoundError()
    active_count = await service._habit_repo.count_active_members(habit_id)  # noqa: SLF001
    return _habit_to_out(habit, active_members_count=active_count)


@router.patch("/habits/{habit_id}", response_model=AdminHabitOut)
async def update_habit(
    habit_id: str,
    payload: AdminHabitUpdateRequest,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitOut:
    """Частичное обновление полей клуба (TZ §3.6.7 — финансовые заморожены)."""
    fields = payload.model_dump(exclude_unset=True)
    # proof_type синхронизируется в HabitService.update на основе proof_types
    # (миграция 012). Если клиент передал только proof_type — Pydantic
    # validator в AdminHabitUpdateRequest уже сконвертировал в [proof_type].
    fields.pop("proof_type", None)
    habit = await service.update(
        admin_id=user.id,
        habit_id=habit_id,
        fields=fields,
    )
    await service._session.commit()  # noqa: SLF001
    active_count = await service._habit_repo.count_active_members(habit_id)  # noqa: SLF001
    return _habit_to_out(habit, active_members_count=active_count)


@router.patch(
    "/habits/{habit_id}/force-financials",
    response_model=AdminHabitForceFinancialsResponse,
)
async def force_financials(
    habit_id: str,
    payload: AdminHabitForceFinancialsRequest,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitForceFinancialsResponse:
    """Force-update price_month / penalty_amount после первого участника.

    Доступно только owner'у — middleware /admin/v1/* уже гейтит доступ.
    Подтверждение (`confirm=true`) обязательно — защита от случайного клика.

    Семантика:
    - Меняет ТОЛЬКО habits.price_month и/или habits.penalty_amount.
    - НЕ трогает: users.deposit_balance, memberships.subscription_until,
      memberships.auto_renew_enabled, memberships.status.
    - Уже оплаченные подписки участников продолжают действовать до
      subscription_until по старой цене. Никто не выгоняется.

    Все force-update логируются как WARN с полным контекстом (audit).
    """
    from datetime import UTC, datetime

    result = await service.force_update_financials(
        admin_id=user.id,
        habit_id=habit_id,
        price_month=payload.price_month,
        penalty_amount=payload.penalty_amount,
        confirm=payload.confirm,
    )
    await service._session.commit()  # noqa: SLF001
    return AdminHabitForceFinancialsResponse(
        ok=True,
        habit_id=habit_id,
        old_price_month=result["old_price_month"],
        new_price_month=result["new_price_month"],
        old_penalty_amount=result["old_penalty_amount"],
        new_penalty_amount=result["new_penalty_amount"],
        updated_at=datetime.now(UTC),
    )


@router.post("/habits/{habit_id}/activate", response_model=AdminHabitActionResponse)
async def activate_habit(
    habit_id: str,
    payload: AdminHabitToggleRequest,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitActionResponse:
    habit = await service.set_active(
        admin_id=user.id,
        habit_id=habit_id,
        is_active=payload.is_active,
    )
    await service._session.commit()  # noqa: SLF001
    return AdminHabitActionResponse(
        ok=True,
        habit_id=str(habit.id),
        is_active=habit.is_active,
        archived_at=habit.archived_at,
    )


@router.post("/habits/{habit_id}/archive", response_model=AdminHabitActionResponse)
async def archive_habit(
    habit_id: str,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitActionResponse:
    habit = await service.archive(admin_id=user.id, habit_id=habit_id)
    await service._session.commit()  # noqa: SLF001
    return AdminHabitActionResponse(
        ok=True,
        habit_id=str(habit.id),
        is_active=habit.is_active,
        archived_at=habit.archived_at,
    )


@router.delete("/habits/{habit_id}", response_model=AdminHabitActionResponse)
async def delete_habit(
    habit_id: str,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitActionResponse:
    """Алиас на archive: семантически «удалить в корзину».

    На уровне БД это soft-delete (заполняется `archived_at`,
    `is_active` остаётся как был). Восстановление — через
    `POST /admin/v1/habits/{id}/restore`.

    Использование: для соответствия REST-семантике DELETE из фронта
    (React Query `useMutation({ method: 'DELETE' })`) и для UI-иконки
    «корзинка» в карточке клуба.
    """
    habit = await service.archive(admin_id=user.id, habit_id=habit_id)
    await service._session.commit()  # noqa: SLF001
    return AdminHabitActionResponse(
        ok=True,
        habit_id=str(habit.id),
        is_active=habit.is_active,
        archived_at=habit.archived_at,
    )


@router.delete("/habits/{habit_id}/permanent")
async def permanent_delete_habit(
    habit_id: str,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> dict:
    """Hard delete клуба из БД.

    Только для архивных клубов и без активных участников. Удаляет
    запись из `habits` (каскад FK снимает memberships/checkins/etc.)
    и из Redis ZSET `bot:available_chats`, чтобы админка не показывала
    группу для перепривязки к удалённому клубу.
    """
    result = await service.permanent_delete(
        admin_id=user.id, habit_id=habit_id
    )

    # Чистим Redis: любая запись с этим chat_id пропадает из выдачи.
    try:
        redis = get_redis()
        chat_id = int(result.get("chat_id") or 0)
        if chat_id:
            await _drop_stale_records(redis, chat_id)
    except Exception:  # noqa: BLE001
        logger.exception("permanent_delete.redis_cleanup_failed")

    await service._session.commit()  # noqa: SLF001
    return result


@router.post("/habits/{habit_id}/restore", response_model=AdminHabitActionResponse)
async def restore_habit(
    habit_id: str,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitActionResponse:
    habit = await service.restore(admin_id=user.id, habit_id=habit_id)
    await service._session.commit()  # noqa: SLF001
    return AdminHabitActionResponse(
        ok=True,
        habit_id=str(habit.id),
        is_active=habit.is_active,
        archived_at=habit.archived_at,
    )


@router.get(
    "/habits/{habit_id}/chat_status",
    response_model=AdminHabitChatStatusResponse,
)
async def get_habit_chat_status(
    habit_id: str,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitChatStatusResponse:
    """Текущее состояние chat_id клуба.

    Используется админкой для кнопки 'Проверить chat_id' после того, как админ
    добавил бота в Telegram-группу. Backend уже получил my_chat_member от бота
    и записал chat_id; админка просто забирает текущее значение.
    """
    habit = await service._habit_repo.get(habit_id)  # noqa: SLF001
    if habit is None:
        from app.core.exceptions import HabitNotFoundError

        raise HabitNotFoundError()
    return AdminHabitChatStatusResponse(
        ok=True,
        habit_id=str(habit.id),
        chat_id=habit.chat_id,
        bound=habit.chat_id != 0,
    )


def _extract_invite_target(invite_link: str) -> str | None:
    """Достаём из ссылки то, что getChat примет как chat_id.

    Telegram Bot API getChat принимает:
    - @username публичного канала/супергруппы
    - t.me/joinchat/AAAA... (старый формат — invite_link как строка)
    - t.me/+AAAA... (новый формат — invite hash)

    Возвращаем строку, которую Telegram примет как chat_id без 400-ки.
    """
    m = _TR_INVITE_RE.match(invite_link.strip())
    if not m:
        return None
    if m.group(1):
        return f"+{m.group(1)}"
    return f"@{m.group(2)}"


@router.post(
    "/habits/preview_chat_by_invite",
    response_model=AdminHabitPreviewChatResponse,
)
async def preview_chat_by_invite(
    payload: AdminHabitPreviewChatRequest,
    user: TelegramUserDep,
    service: HabitServiceDep,
) -> AdminHabitPreviewChatResponse:
    """Резолв Telegram-чата по инвайт-ссылке через Bot API.

    Используется в форме создания клуба ДО сохранения в БД. Алгоритм:
    1. Парсим ссылку → chat_id в формате @username / +hash.
    2. Дёргаем getChat у основного бота (он должен быть в группе).
    3. Возвращаем {chat_id, title, type}. Проверяем, не привязан ли
       chat_id уже к другому клубу.
    """
    from app.core.config import get_settings

    target = _extract_invite_target(payload.invite_link)
    if target is None:
        return AdminHabitPreviewChatResponse(
            ok=False,
            invite_link=payload.invite_link,
            code="invalid_invite_link",
            message="Ссылка должна быть вида https://t.me/+xxx или https://t.me/username",
        )

    settings = get_settings()
    bot_token = settings.bot_token
    if not bot_token:
        logger.error("preview_chat.no_bot_token")
        return AdminHabitPreviewChatResponse(
            ok=False,
            invite_link=payload.invite_link,
            code="bot_token_missing",
            message="BOT_TOKEN не настроен на бэкенде",
        )

    api_url = f"https://api.telegram.org/bot{bot_token}/getChat"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        ) as session:
            async with session.get(api_url, params={"chat_id": target}) as resp:
                data = await resp.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError) as exc:
        logger.warning("preview_chat.http_failed", extra={"error": str(exc)})
        return AdminHabitPreviewChatResponse(
            ok=False,
            invite_link=payload.invite_link,
            code="telegram_unreachable",
            message="Не удалось связаться с Telegram. Попробуй ещё раз.",
        )

    if not data.get("ok"):
        description = data.get("description", "Telegram вернул ошибку")
        code = "telegram_error"
        if "chat not found" in description.lower():
            code = "bot_not_in_chat"
            description = (
                "Бот @join_prideclub_bot ещё не добавлен в эту группу. "
                "Добавь его и нажми «Проверить» ещё раз."
            )
        elif "invite hash" in description.lower() or "expired" in description.lower():
            code = "invite_link_expired"
            description = "Инвайт-ссылка устарела. Создай новую в группе."
        logger.info(
            "preview_chat.telegram_error",
            extra={"code": code, "description": description},
        )
        return AdminHabitPreviewChatResponse(
            ok=False,
            invite_link=payload.invite_link,
            code=code,
            message=description,
        )

    result = data.get("result", {})
    chat_id = int(result.get("id", 0))
    title = result.get("title") or result.get("username") or None
    chat_type = result.get("type")

    existing = await service._habit_repo.get_by_chat_id(chat_id)  # noqa: SLF001
    if existing is not None:
        return AdminHabitPreviewChatResponse(
            ok=True,
            chat_id=chat_id,
            title=title,
            type=chat_type,
            invite_link=payload.invite_link,
            already_used_by_habit_id=str(existing.id),
            code="already_bound",
            message=f"Этот чат уже привязан к клубу «{existing.title}»",
        )

    return AdminHabitPreviewChatResponse(
        ok=True,
        chat_id=chat_id,
        title=title,
        type=chat_type,
        invite_link=payload.invite_link,
    )

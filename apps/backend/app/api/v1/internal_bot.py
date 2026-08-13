"""Internal endpoint: бот → backend при my_chat_member.

Бот шлёт сюда событие 'бот добавлен в Telegram-чат клуба'. Backend:
1. Всегда пишет событие в Redis ZSET `bot:available_chats` (TTL 7 дней) —
   админка через GET /admin/v1/habits/available_chats показывает список
   групп, куда бот добавлен, чтобы админ мог выбрать нужную и подставить
   chat_id в форму создания клуба. Это нужно потому что Bot API getChat
   по приватной invite-ссылке не резолвит чат, если бот был добавлен
   другим способом.
2. Матчит chat_id ↔ habit через invite_link (если есть) и обновляет
   habits.chat_id.

Если matching habit уже имеет chat_id=0 (только что создан через админку)
и invite_link совпал — обновляем chat_id, возвращаем habit_id.
Если habit с таким invite_link не найден — логируем warning, возвращаем code.
Если у habit chat_id уже не 0 и не совпадает с присланным — отказываем
(защита от случайного матча двух клубов на одну ссылку).
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.v1.users import ServiceCallerDep
from app.core.deps import SessionDep
from app.core.logging import get_logger
from app.db.redis import get_redis
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository

router = APIRouter()
logger = get_logger("internal_bot")


AVAILABLE_CHATS_KEY = "bot:available_chats"
AVAILABLE_CHATS_TTL_SECONDS = 7 * 24 * 3600  # 7 дней


class BotChatAddedRequest(BaseModel):
    chat_id: int
    chat_title: str | None = None
    chat_type: str | None = None
    invite_link: str | None = None
    actor_user_id: int | None = None


class BotChatAddedResponse(BaseModel):
    ok: bool
    habit_id: str | None = None
    chat_id: int | None = None
    code: str | None = None


async def _record_available_chat(
    *,
    chat_id: int,
    chat_title: str | None,
    chat_type: str | None,
    invite_link: str | None,
    actor_user_id: int | None,
) -> None:
    """Сохраняет факт my_chat_member в Redis ZSET для админки.

    Score = unix timestamp, member = JSON. TTL ключа обновляется при каждой
    записи. Используется для UI «выбрать чат, куда бот добавлен».

    Перед записью удаляет предыдущие записи с тем же chat_id, чтобы
    название/тип всегда были актуальные.
    """
    log = get_logger("bot_chat_added.cache")
    try:
        redis = get_redis()
        await _drop_stale_records(redis, chat_id)
        payload_json = json.dumps(
            {
                "chat_id": chat_id,
                "chat_title": chat_title,
                "chat_type": chat_type,
                "invite_link": invite_link,
                "actor_user_id": actor_user_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        now = time.time()
        async with redis.pipeline(transaction=False) as pipe:
            pipe.zadd(AVAILABLE_CHATS_KEY, {payload_json: now})
            pipe.expire(AVAILABLE_CHATS_KEY, AVAILABLE_CHATS_TTL_SECONDS)
            await pipe.execute()
    except Exception as exc:  # noqa: BLE001 — кэш не должен ломать основной flow
        log.warning(
            "available_chat_cache_failed",
            extra={"err": str(exc), "kind": "redis"},
        )


async def _drop_stale_records(redis: object, chat_id: int) -> int:
    """Удаляет все записи в ZSET с заданным chat_id, чтобы не было дублей.

    Возвращает количество удалённых записей (0, если ничего не было
    или если Redis временно недоступен). Никогда не бросает исключений —
    Redis-сбой не должен валить webhook-обработчик, иначе бот
    перестаёт посылать `/internal/bot/chat_removed` и старые записи
    в Redis живут вечно.
    """
    try:
        raw_items = await redis.zrange(AVAILABLE_CHATS_KEY, 0, -1)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        logger.exception("_drop_stale_records.zrange_failed", extra={"chat_id": chat_id})
        return 0

    to_delete: list[str] = []
    for raw in raw_items:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("chat_id") == chat_id:
            to_delete.append(raw)
    if to_delete:
        try:
            await redis.zrem(AVAILABLE_CHATS_KEY, *to_delete)  # type: ignore[attr-defined]  # noqa: SLF001
        except Exception:  # noqa: BLE001
            logger.exception("_drop_stale_records.zrem_failed", extra={"chat_id": chat_id})
            return 0
    return len(to_delete)


@router.post("/bot/chat_added", response_model=BotChatAddedResponse)
async def bot_chat_added(
    payload: BotChatAddedRequest,
    session: SessionDep,
    _: ServiceCallerDep,
) -> BotChatAddedResponse:
    """Обработка my_chat_member от бота."""
    log = get_logger("bot_chat_added")

    await _record_available_chat(
        chat_id=payload.chat_id,
        chat_title=payload.chat_title,
        chat_type=payload.chat_type,
        invite_link=payload.invite_link,
        actor_user_id=payload.actor_user_id,
    )

    repo = HabitRepository(session)
    habit = None

    if payload.invite_link:
        habit = await repo.get_by_invite_link(payload.invite_link)

    if habit is None:
        log.warning(
            "bot_chat_added_no_match",
            extra={
                "chat_id": payload.chat_id,
                "invite_link": payload.invite_link,
                "actor_user_id": payload.actor_user_id,
            },
        )
        return BotChatAddedResponse(
            ok=False,
            chat_id=payload.chat_id,
            code="habit_not_matched",
        )

    if habit.chat_id != 0 and habit.chat_id != payload.chat_id:
        log.warning(
            "bot_chat_added_already_bound",
            extra={
                "habit_id": str(habit.id),
                "habit_chat_id": habit.chat_id,
                "incoming_chat_id": payload.chat_id,
            },
        )
        return BotChatAddedResponse(
            ok=False,
            habit_id=str(habit.id),
            chat_id=payload.chat_id,
            code="habit_already_bound_to_other_chat",
        )

    if habit.chat_id == payload.chat_id:
        return BotChatAddedResponse(
            ok=True,
            habit_id=str(habit.id),
            chat_id=payload.chat_id,
        )

    habit.chat_id = payload.chat_id
    await session.commit()

    log.info(
        "bot_chat_added_bound",
        extra={
            "habit_id": str(habit.id),
            "chat_id": payload.chat_id,
            "invite_link": payload.invite_link,
            "actor_user_id": payload.actor_user_id,
        },
    )
    return BotChatAddedResponse(
        ok=True,
        habit_id=str(habit.id),
        chat_id=payload.chat_id,
    )


class BotChatRemovedRequest(BaseModel):
    chat_id: int
    actor_user_id: int | None = None


class BotChatRemovedResponse(BaseModel):
    ok: bool
    chat_id: int
    habit_id: str | None = None
    code: str | None = None


@router.post("/bot/chat_removed", response_model=BotChatRemovedResponse)
async def bot_chat_removed(
    payload: BotChatRemovedRequest,
    session: SessionDep,
    _: ServiceCallerDep,
) -> BotChatRemovedResponse:
    """Обработка my_chat_member при удалении бота из чата.

    Действия:
    1. Удаляем записи из Redis ZSET `bot:available_chats` по этому chat_id
       (список чатов в админке больше не должен показывать удалённую группу).
    2. Если чат был привязан к клубу — сбрасываем habit.chat_id = 0,
       чтобы админ видел «не привязан» и мог выбрать новую группу.
    """
    log = get_logger("bot_chat_removed")

    redis = get_redis()
    removed_count = await _drop_stale_records(redis, payload.chat_id)
    log.info(
        "bot_chat_removed.redis_dropped",
        extra={"chat_id": payload.chat_id, "removed_records": removed_count},
    )

    repo = HabitRepository(session)
    habit = await repo.get_by_chat_id(payload.chat_id)
    if habit is not None and habit.chat_id == payload.chat_id:
        habit.chat_id = 0
        await session.commit()
        log.info(
            "bot_chat_removed.habit_unbound",
            extra={
                "habit_id": str(habit.id),
                "chat_id": payload.chat_id,
                "actor_user_id": payload.actor_user_id,
            },
        )
        return BotChatRemovedResponse(
            ok=True,
            chat_id=payload.chat_id,
            habit_id=str(habit.id),
            code="habit_unbound",
        )

    return BotChatRemovedResponse(
        ok=True,
        chat_id=payload.chat_id,
        code="redis_only" if removed_count > 0 else "noop",
    )


# ---------- HabitState: pre-filter для бота (PR №9) --------------------------
#
# Бот вызывает этот endpoint ПЕРЕД отправкой чек-ина в /internal/checkins/process.
# Возвращает всё, что бот должен знать, чтобы:
# - отвергнуть неподдерживаемый тип proof (pre-filter по proof_types),
# - отвергнуть повторный чек-ин за сегодня (pre-filter по дубликату).
#
# Почему НЕ дожидаемся worker: backend возвращает {ok: True, task_id: ...}
# сразу после send_task() — а worker отвергает задачу асинхронно, и бот
# про это не узнаёт (он уже ответил юзеру "Принято"). Pre-filter в боте —
# единственный способ показать правильное сообщение.


class HabitStateResponse(BaseModel):
    found: bool
    habit_id: str | None = None
    proof_types: list[str] = Field(default_factory=list)
    checkin_topic_thread_id: int | None = None
    already_checked_in: bool = False
    checked_in_at: datetime | None = None
    # Feature/paused-member-ux: бот prefilter должен отвечать
    # "пополни депозит" для PAUSED-членов, а не общее "окно закрыто".
    # Default None = нет membership → бот НЕ реагирует (defense-in-depth
    # в /checkins/process всё равно отвергнет no_membership).
    membership_status: str | None = None  # "active" | "paused" | "left" | None
    # Депозит пользователя (копейки). Нужен для REJECT_PAUSED_OR_WINDOW
    # текста "{balance} из {penalty}". Default 0 = если state не подгрузился,
    # defense-in-depth в /checkins/process всё равно отвергнет.
    deposit_balance: int = 0
    # Штраф клуба (копейки). Нужен боту для той же copy "{balance} из
    # {penalty}". Default 0 = если клуб не подгрузился, бот покажет
    # "из 0,00 ₽" (не критично, defence-in-depth).
    penalty_amount: int = 0


@router.get("/bot/habit_state", response_model=HabitStateResponse)
async def get_habit_state(
    session: SessionDep,
    _: ServiceCallerDep,
    chat_id: int = Query(..., description="Telegram chat_id супергруппы клуба"),
    user_id: int = Query(..., description="telegram_id пользователя"),
) -> HabitStateResponse:
    """Состояние клуба и членства для бота.

    Если клуб не найден или бот не привязан к чату — `found=False`,
    остальные поля пустые. Бот в этом случае должен молчать (как при
    `habit_not_found`).

    Если membership пользователя в клубе нет — `found=True`, но
    `already_checked_in=False` (бот пропустит пре-фильтр дубликата,
    и worker отвергнет чек-ин как no_membership).
    """
    log = get_logger("bot_habit_state")

    habit_repo = HabitRepository(session)
    habit = await habit_repo.get_by_chat_id(chat_id)
    if habit is None or habit.chat_id == 0:
        log.warning(
            "habit_state_habit_not_found",
            extra={"chat_id": chat_id, "user_id": user_id},
        )
        return HabitStateResponse(found=False)

    membership_repo = MembershipRepository(session)
    membership = await membership_repo.get_for_user_in_habit(
        user_id=user_id, habit_id=habit.id
    )

    # Feature/paused-member-ux: deposit_balance юзера — для бота copy.
    user_repo = UserRepository(session)
    user_row = await user_repo.get(user_id)

    already_checked_in = False
    checked_in_at: datetime | None = None
    if membership is not None:
        club_date_now = habit.club_date(datetime.now(tz=UTC))
        checkin = await CheckinRepository(session).get_for_date(
            membership_id=membership.id,
            on_date=club_date_now,
        )
        if checkin is not None:
            already_checked_in = True
            checked_in_at = checkin.verified_at

    return HabitStateResponse(
        found=True,
        habit_id=str(habit.id),
        proof_types=list(habit.proof_types or []),
        checkin_topic_thread_id=habit.checkin_topic_thread_id,
        already_checked_in=already_checked_in,
        checked_in_at=checked_in_at,
        # Feature/paused-member-ux: для бота — paused-юзер должен получить
        # "пополни депозит", а не общее "окно закрыто".
        membership_status=membership.status.value if membership is not None else None,
        deposit_balance=user_row.deposit_balance if user_row is not None else 0,
        penalty_amount=habit.penalty_amount,
    )


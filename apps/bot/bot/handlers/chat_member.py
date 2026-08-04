"""Обработчик my_chat_member: бот добавлен/удалён из чата клуба.

Когда админ добавляет бота в Telegram-группу клуба, мы ловим событие
ChatMemberUpdated (new status: MEMBER/ADMINISTRATOR). Из события берём
chat.id и invite_link.invite_link (если есть) — и шлём в backend через
/internal/bot/chat_added. Backend матчит invite_link → habit и подставляет
chat_id в habits.chat_id автоматически.

Когда бота удаляют (или он сам выходит) — шлём /internal/bot/chat_removed,
чтобы backend убрал запись из Redis (иначе список чатов в админке будет
содержать уже неактуальные группы).

Auth: X-Service-Token (тот же секрет, что и /internal/*).
"""
from __future__ import annotations

from typing import Any

import aiohttp
from aiogram import Bot, Router
from aiogram.filters import IS_MEMBER, IS_NOT_MEMBER, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated
from security import generate_service_token

from bot.config import get_settings
from bot.logging_setup import get_logger

router = Router(name="chat_member")
log = get_logger("bot.chat_member")


async def _post_internal(
    *,
    path: str,
    body: dict[str, Any],
) -> None:
    """Общий хелпер: POST во внутренний API backend с service-token."""
    settings = get_settings()
    token = generate_service_token(
        service_name="bot",
        target_audience="backend-api",
        secret=settings.service_secret,
        ttl_seconds=60,
    )
    headers = {"X-Service-Token": token, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.post(
                f"{settings.backend_url}{path}",
                json=body,
                headers=headers,
            ) as resp:
                data = await resp.json()
                log.info(
                    "internal_dispatched",
                    extra={
                        "path": path,
                        "chat_id": body.get("chat_id"),
                        "status": resp.status,
                        "code": data.get("code"),
                    },
                )
    except aiohttp.ClientError as exc:
        log.error(
            "internal_dispatch_failed",
            extra={"err": str(exc), "kind": "network", "path": path},
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "internal_dispatch_failed",
            extra={"err": str(exc), "kind": "unexpected", "path": path},
        )


async def _notify_backend_chat_added(
    *,
    chat_id: int,
    chat_title: str | None,
    chat_type: str | None,
    invite_link: str | None,
    actor_user_id: int | None,
) -> None:
    await _post_internal(
        path="/internal/bot/chat_added",
        body={
            "chat_id": chat_id,
            "chat_title": chat_title,
            "chat_type": chat_type,
            "invite_link": invite_link,
            "actor_user_id": actor_user_id,
        },
    )


async def _notify_backend_chat_removed(
    *,
    chat_id: int,
    actor_user_id: int | None,
) -> None:
    await _post_internal(
        path="/internal/bot/chat_removed",
        body={"chat_id": chat_id, "actor_user_id": actor_user_id},
    )


@router.my_chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def on_bot_added_to_chat(event: ChatMemberUpdated, bot: Bot) -> None:
    """Бот добавлен в группу/канал как MEMBER/ADMINISTRATOR."""
    invite_link_str: str | None = None
    if event.invite_link is not None:
        invite_link_str = event.invite_link.invite_link

    log.info(
        "bot_added_to_chat",
        extra={
            "chat_id": event.chat.id,
            "chat_title": event.chat.title,
            "chat_type": event.chat.type,
            "invite_link": invite_link_str,
            "actor_user_id": event.from_user.id if event.from_user else None,
        },
    )

    await _notify_backend_chat_added(
        chat_id=event.chat.id,
        chat_title=event.chat.title,
        chat_type=event.chat.type,
        invite_link=invite_link_str,
        actor_user_id=event.from_user.id if event.from_user else None,
    )


@router.my_chat_member(ChatMemberUpdatedFilter(IS_MEMBER >> IS_NOT_MEMBER))
async def on_bot_removed_from_chat(event: ChatMemberUpdated, bot: Bot) -> None:
    """Бот удалён из группы/канала (статус сменился на IS_NOT_MEMBER)."""
    log.info(
        "bot_removed_from_chat",
        extra={
            "chat_id": event.chat.id,
            "chat_title": event.chat.title,
            "new_status": event.new_chat_member.status,
            "actor_user_id": event.from_user.id if event.from_user else None,
        },
    )
    await _notify_backend_chat_removed(
        chat_id=event.chat.id,
        actor_user_id=event.from_user.id if event.from_user else None,
    )

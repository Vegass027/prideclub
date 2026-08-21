"""HabitService — админский флоу управления клубами (TZ §3.6).

Все операции принимают `AsyncSession` через конструктор (DI). Сервис НЕ
вызывает `session.commit()` — это делает middleware/handler.

Управляет:
- `create` — создание клуба (всегда с `is_active=false`, чтобы владелец проверил)
- `update` — частичное обновление полей; финансовые поля заморожены после первого вступления
- `archive` — soft-delete (`is_active=false`, `archived_at=now()`)
- `restore` — снимает архив, но НЕ активирует (явный /activate)
- `set_active` — тумблер is_active

Валидация — внутри, не в роутах. Все ошибки через доменные исключения
(`HabitValidationError`, `HabitNotFoundError`, `HabitInactiveError`,
`HabitArchivedError`).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.constants import PROOF_TYPE_VALUES, ProofType
from app.core.exceptions import (
    HabitArchivedError,
    HabitNotFoundError,
    HabitTopicDuplicateError,
    HabitTopicMismatchError,
    HabitValidationError,
)
from app.core.logging import get_logger
from app.core.telegram_links import parse_telegram_topic_link
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository

_TELEGRAM_INVITE_RE = re.compile(r"^https://(t\.me|telegram\.me)/[A-Za-z0-9_+\-/]+$")


class HabitService:
    def __init__(
        self,
        session,
        habit_repo: HabitRepository,
        membership_repo: MembershipRepository,
    ) -> None:
        self._session = session
        self._habit_repo = habit_repo
        self._membership_repo = membership_repo
        self._logger = get_logger("habit_service")

    async def create(
        self,
        *,
        admin_id: int,
        title: str,
        description: str | None,
        photo_url: str | None,
        telegram_invite_link: str | None,
        stat_name: str,
        stat_icon: str | None,
        chat_id: int,
        checkin_window_start: Any,
        checkin_window_end: Any,
        timezone_str: str,
        proof_types: list[str],
        price_month: int,
        penalty_amount: int,
        # Pravki-catcher-deposit (Phase 1 Task 1.5, 2026-08-21): сумма ловцу
        # от штрафа в копейках. DEFAULT 0 = для новых клубов поведение
        # "всё в фонд" по умолчанию (обратная совместимость).
        catcher_amount_kopecks: int = 0,
        stat_gain_per_checkin: int,
        stat_loss_per_miss: int,
        member_limit: int | None,
        curator_id: int | None,
        checkin_topic_link: str,
        notifications_topic_link: str,
        chat_topic_link: str | None = None,
    ) -> Any:
        """Создать клуб. Всегда с `is_active=False` (TZ §3.6.4).

        Topic-scoped: обязательны ссылки на топики чек-инов и
        уведомлений (https://t.me/c/<chat_id>/<thread_id>).
        Опционально — топик общего чата клуба в той же группе.
        Бот ничего не создаёт — топики делает владелец в Telegram.

        Multi-proof (migration 012): `proof_types` — массив 1..3 строк
        из {"video_note", "photo", "text"}. `proof_type` в БД
        выставляется как `proof_types[0]` (для обратной совместимости
        со старыми клиентами Bot API).
        """
        _validate_title(title)
        _validate_stat_name(stat_name)
        if stat_icon is not None:
            _validate_stat_icon(stat_icon)
        _validate_telegram_invite_link(telegram_invite_link)
        _validate_timezone(timezone_str)
        _validate_checkin_window(checkin_window_start, checkin_window_end)
        _validate_prices(price_month=price_month, penalty_amount=penalty_amount)
        _validate_stat_gains(
            stat_gain_per_checkin=stat_gain_per_checkin,
            stat_loss_per_miss=stat_loss_per_miss,
        )
        _validate_member_limit(member_limit)
        _validate_proof_types(proof_types)

        checkin_topic = parse_telegram_topic_link(checkin_topic_link)
        notifications_topic = parse_telegram_topic_link(notifications_topic_link)

        if chat_id != 0 and chat_id != checkin_topic.chat_id:
            raise HabitTopicMismatchError(
                "chat_id в ссылке на топик чек-инов не совпадает с chat_id клуба"
            )
        if chat_id != 0 and chat_id != notifications_topic.chat_id:
            raise HabitTopicMismatchError(
                "chat_id в ссылке на топик уведомлений не совпадает с chat_id клуба"
            )
        if checkin_topic.thread_id == notifications_topic.thread_id:
            raise HabitValidationError(
                "Топик чек-инов и топик уведомлений должны различаться",
                code="habit_topics_must_differ",
            )

        chat_topic_thread_id: int | None = None
        if chat_topic_link is not None and chat_topic_link.strip():
            chat_topic = parse_telegram_topic_link(chat_topic_link)
            if chat_topic.chat_id != checkin_topic.chat_id:
                raise HabitTopicMismatchError(
                    "Ссылка на топик чата указывает на группу, отличную от "
                    "группы топиков чек-инов и уведомлений"
                )
            if chat_topic.thread_id in (
                checkin_topic.thread_id,
                notifications_topic.thread_id,
            ):
                raise HabitValidationError(
                    "Топик чата должен отличаться от топика чек-инов "
                    "и топика уведомлений",
                    code="habit_topics_must_differ",
                )
            chat_topic_thread_id = chat_topic.thread_id

        resolved_chat_id = (
            chat_id if chat_id != 0 else checkin_topic.chat_id
        )

        existing = await self._habit_repo.get_by_chat_id(resolved_chat_id)
        if existing is not None:
            raise HabitValidationError(
                f"Клуб с chat_id={resolved_chat_id} уже существует",
                code="habit_chat_id_duplicate",
            )

        duplicate = await self._habit_repo.get_by_chat_and_thread(
            checkin_topic.chat_id, checkin_topic.thread_id
        )
        if duplicate is not None:
            raise HabitTopicDuplicateError(
                f"Топик {checkin_topic.thread_id} уже привязан к клубу "
                f"«{duplicate.title}»"
            )
        duplicate_notif = await self._habit_repo.get_by_chat_and_thread(
            notifications_topic.chat_id, notifications_topic.thread_id
        )
        if duplicate_notif is not None:
            raise HabitTopicDuplicateError(
                f"Топик уведомлений {notifications_topic.thread_id} "
                f"уже привязан к клубу «{duplicate_notif.title}»"
            )

        fields: dict[str, Any] = {
            "title": title.strip(),
            "description": description,
            "chat_id": resolved_chat_id,
            "checkin_window_start": checkin_window_start,
            "checkin_window_end": checkin_window_end,
            "timezone": timezone_str,
            "proof_types": proof_types,
            "proof_type": ProofType(proof_types[0]),
            "penalty_amount": penalty_amount,
            "price_month": price_month,
            # Pravki-catcher-deposit (Phase 1 Task 1.5): новое поле, передаём
            # в fields dict для INSERT. Валидация ge=0 на уровне Pydantic-схемы
            # (AdminHabitCreateRequest.catcher_amount_kopecks).
            "catcher_amount_kopecks": catcher_amount_kopecks,
            "prize_pool": 0,
            "is_active": False,
            "photo_url": photo_url,
            "telegram_invite_link": telegram_invite_link,
            "stat_name": stat_name.strip(),
            "stat_icon": stat_icon,
            "stat_gain_per_checkin": stat_gain_per_checkin,
            "stat_loss_per_miss": stat_loss_per_miss,
            "member_limit": member_limit,
            "curator_id": curator_id,
            "archived_at": None,
            "checkin_topic_thread_id": checkin_topic.thread_id,
            "notifications_topic_thread_id": notifications_topic.thread_id,
            "chat_topic_thread_id": chat_topic_thread_id,
        }
        habit = await self._habit_repo.create(fields=fields)

        self._logger.info(
            "habit_created",
            extra={
                "admin_id": admin_id,
                "habit_id": habit.id,
                "title": habit.title,
                "chat_id": habit.chat_id,
                "checkin_topic_thread_id": checkin_topic.thread_id,
                "notifications_topic_thread_id": notifications_topic.thread_id,
                "chat_topic_thread_id": chat_topic_thread_id,
            },
        )
        return habit

    async def update(
        self,
        *,
        admin_id: int,
        habit_id: str,
        fields: dict[str, Any],
    ) -> Any:
        """Частичное обновление. Финансовые поля — только если клуб пустой."""
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()
        if habit.archived_at is not None:
            raise HabitArchivedError()

        if "title" in fields:
            _validate_title(fields["title"])
            fields["title"] = fields["title"].strip()
        if "stat_name" in fields:
            _validate_stat_name(fields["stat_name"])
            fields["stat_name"] = fields["stat_name"].strip()
        if "stat_icon" in fields:
            _validate_stat_icon(fields["stat_icon"])
        if "telegram_invite_link" in fields:
            _validate_telegram_invite_link(fields["telegram_invite_link"])
        if "timezone" in fields:
            _validate_timezone(fields["timezone"])
        if "proof_types" in fields:
            _validate_proof_types(fields["proof_types"])
            # Синхронизируем proof_type (первый элемент) для обратной
            # совместимости со старыми клиентами.
            fields["proof_type"] = ProofType(fields["proof_types"][0])
        if "checkin_window_start" in fields or "checkin_window_end" in fields:
            start = fields.get("checkin_window_start", habit.checkin_window_start)
            end = fields.get("checkin_window_end", habit.checkin_window_end)
            _validate_checkin_window(start, end)
        if "price_month" in fields or "penalty_amount" in fields:
            new_price = fields.get("price_month", habit.price_month)
            new_penalty = fields.get("penalty_amount", habit.penalty_amount)
            _validate_prices(price_month=new_price, penalty_amount=new_penalty)
        if (
            "stat_gain_per_checkin" in fields
            or "stat_loss_per_miss" in fields
        ):
            new_gain = fields.get(
                "stat_gain_per_checkin", habit.stat_gain_per_checkin
            )
            new_loss = fields.get(
                "stat_loss_per_miss", habit.stat_loss_per_miss
            )
            _validate_stat_gains(
                stat_gain_per_checkin=new_gain,
                stat_loss_per_miss=new_loss,
            )
        if "member_limit" in fields:
            _validate_member_limit(fields["member_limit"])

        topic_link_changed = (
            "checkin_topic_link" in fields
            or "notifications_topic_link" in fields
            or "chat_topic_link" in fields
        )
        if topic_link_changed:
            new_checkin_link = fields.pop("checkin_topic_link", None)
            new_notifications_link = fields.pop(
                "notifications_topic_link", None
            )
            new_chat_topic_link = fields.pop("chat_topic_link", None)

            existing_chat_id = habit.chat_id

            if new_checkin_link is not None:
                topic = parse_telegram_topic_link(new_checkin_link)
                fields["checkin_topic_thread_id"] = topic.thread_id
                if existing_chat_id == 0:
                    fields["chat_id"] = topic.chat_id
                    existing_chat_id = topic.chat_id
                elif topic.chat_id != existing_chat_id:
                    raise HabitTopicMismatchError(
                        "Топик чек-инов находится в другой группе "
                        "(chat_id в ссылке не совпадает с чатом клуба). "
                        "Укажи ссылку на топик внутри той же группы, "
                        "что привязана к клубу."
                    )
            else:
                fields["checkin_topic_thread_id"] = (
                    habit.checkin_topic_thread_id
                )

            if new_notifications_link is not None:
                topic = parse_telegram_topic_link(new_notifications_link)
                fields["notifications_topic_thread_id"] = topic.thread_id
                if existing_chat_id == 0:
                    fields["chat_id"] = topic.chat_id
                    existing_chat_id = topic.chat_id
                elif topic.chat_id != existing_chat_id:
                    raise HabitTopicMismatchError(
                        "Топик уведомлений находится в другой группе "
                        "(chat_id в ссылке не совпадает с чатом клуба). "
                        "Укажи ссылку на топик внутри той же группы, "
                        "что привязана к клубу."
                    )
            else:
                fields["notifications_topic_thread_id"] = (
                    habit.notifications_topic_thread_id
                )

            if new_chat_topic_link is not None and new_chat_topic_link.strip():
                chat_topic = parse_telegram_topic_link(new_chat_topic_link)
                if existing_chat_id == 0:
                    fields["chat_id"] = chat_topic.chat_id
                    existing_chat_id = chat_topic.chat_id
                elif chat_topic.chat_id != existing_chat_id:
                    raise HabitTopicMismatchError(
                        "Топик чата находится в другой группе "
                        "(chat_id в ссылке не совпадает с чатом клуба)."
                    )
                fields["chat_topic_thread_id"] = chat_topic.thread_id
            elif "chat_topic_link" in fields:
                fields["chat_topic_thread_id"] = None
            else:
                fields["chat_topic_thread_id"] = habit.chat_topic_thread_id

            resolved_chat_id = fields.get("chat_id", existing_chat_id)

            if (
                fields["checkin_topic_thread_id"]
                == fields["notifications_topic_thread_id"]
            ):
                raise HabitValidationError(
                    "Топик чек-инов и топик уведомлений должны различаться",
                    code="habit_topics_must_differ",
                )

            chat_topic_tid = fields.get("chat_topic_thread_id")
            if chat_topic_tid is not None and chat_topic_tid in (
                fields["checkin_topic_thread_id"],
                fields["notifications_topic_thread_id"],
            ):
                raise HabitValidationError(
                    "Топик чата должен отличаться от топика чек-инов "
                    "и топика уведомлений",
                    code="habit_topics_must_differ",
                )

            for thread_id in (
                fields["checkin_topic_thread_id"],
                fields["notifications_topic_thread_id"],
                fields.get("chat_topic_thread_id"),
            ):
                if thread_id is None:
                    continue
                dup = await self._habit_repo.get_by_chat_and_thread(
                    resolved_chat_id, thread_id
                )
                if dup is not None and str(dup.id) != habit_id:
                    raise HabitTopicDuplicateError(
                        f"Топик {thread_id} уже привязан к клубу «{dup.title}»"
                    )

        # Заморозка price_month/penalty_amount после первого участника СНЯТА.
        # Middleware /admin/v1/* уже гейтит доступ только owner'у —
        # владелец может менять цену когда угодно (типичный use case:
        # поднять цену с нового месяца, см. force_update_financials для
        # targeted-обновления только финансов).
        update_fields = dict(fields)

        updated = await self._habit_repo.update(habit, fields=update_fields)
        self._logger.info(
            "habit_updated",
            extra={
                "admin_id": admin_id,
                "habit_id": habit_id,
                "changed_fields": sorted(update_fields.keys()),
            },
        )
        return updated

    async def archive(self, *, admin_id: int, habit_id: str) -> Any:
        """Soft-delete: is_active=false, archived_at=now()."""
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()
        if habit.archived_at is not None:
            return habit
        now = datetime.now(UTC)
        await self._habit_repo.archive(habit, archived_at=now)
        self._logger.info(
            "habit_archived",
            extra={
                "admin_id": admin_id,
                "habit_id": habit_id,
                "archived_at": now.isoformat(),
            },
        )
        return habit

    async def restore(self, *, admin_id: int, habit_id: str) -> Any:
        """Снять архив. is_active остаётся false — нужен явный /activate."""
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()
        if habit.archived_at is None:
            return habit
        await self._habit_repo.restore(habit)
        self._logger.info(
            "habit_restored",
            extra={"admin_id": admin_id, "habit_id": habit_id},
        )
        return habit

    async def permanent_delete(self, *, admin_id: int, habit_id: str) -> dict:
        """Полное удаление клуба из БД (hard delete).

        Разрешено только если у клуба нет активных memberships
        (status != 'left'), иначе участники потеряют финансовые
        данные. Каскадные FK на memberships/checkins/penalties
        удалят связанные строки автоматически.
        """
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()

        active_members = await self._habit_repo.count_active_members(habit_id)
        if active_members > 0:
            raise HabitValidationError(
                "Невозможно удалить клуб навсегда: у него есть активные "
                "участники. Сначала выгоните их или дождитесь выхода.",
                code="habit_has_active_members",
            )

        chat_id = habit.chat_id
        title = habit.title
        await self._habit_repo.permanent_delete(habit)

        self._logger.info(
            "habit_permanently_deleted",
            extra={
                "admin_id": admin_id,
                "habit_id": habit_id,
                "title": title,
                "chat_id": chat_id,
            },
        )

        return {
            "ok": True,
            "habit_id": habit_id,
            "chat_id": chat_id,
            "code": "habit_permanently_deleted",
        }

    async def set_active(
        self,
        *,
        admin_id: int,
        habit_id: str,
        is_active: bool,
    ) -> Any:
        """Тумблер is_active. Активация архивного клуба запрещена."""
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()
        if is_active and habit.archived_at is not None:
            raise HabitArchivedError()
        if habit.is_active == is_active:
            return habit
        await self._habit_repo.set_active(habit, is_active=is_active)
        self._logger.info(
            "habit_active_toggled",
            extra={
                "admin_id": admin_id,
                "habit_id": habit_id,
                "is_active": is_active,
            },
        )
        return habit

    async def force_update_financials(
        self,
        *,
        admin_id: int,
        habit_id: str,
        price_month: int | None,
        penalty_amount: int | None,
        confirm: bool,
    ) -> dict[str, Any]:
        """Force-update price_month / penalty_amount вне заморозки.

        Доступно только owner (см. middleware /admin/v1/*).

        Что меняет: только habits.price_month и/или habits.penalty_amount.
        Что НЕ меняет: users.deposit_balance, memberships.subscription_until,
        memberships.auto_renew_enabled, memberships.status. Уже оплаченные
        подписки участников продолжают действовать по старой цене до конца
        оплаченного периода.

        Возвращает dict со старыми/новыми значениями для UI-подтверждения.
        """
        if not confirm:
            raise HabitValidationError(
                "Требуется подтверждение (confirm=true) — это защита "
                "от случайного изменения финансов клуба",
                code="habit_force_financials_confirm_required",
            )
        if price_month is None and penalty_amount is None:
            raise HabitValidationError(
                "Укажи хотя бы одно поле: price_month или penalty_amount",
                code="habit_force_financials_no_fields",
            )
        if price_month is not None and price_month <= 0:
            raise HabitValidationError(
                "price_month должен быть положительным INTEGER (копейки)",
                code="habit_price_invalid",
            )
        if penalty_amount is not None and penalty_amount <= 0:
            raise HabitValidationError(
                "penalty_amount должен быть положительным INTEGER (копейки)",
                code="habit_penalty_invalid",
            )

        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()
        if habit.archived_at is not None:
            raise HabitArchivedError()

        old_price_month = int(habit.price_month)
        old_penalty_amount = int(habit.penalty_amount)

        update_fields: dict[str, Any] = {}
        if price_month is not None and price_month != old_price_month:
            update_fields["price_month"] = price_month
        if penalty_amount is not None and penalty_amount != old_penalty_amount:
            update_fields["penalty_amount"] = penalty_amount

        if not update_fields:
            return {
                "habit": habit,
                "old_price_month": old_price_month,
                "new_price_month": old_price_month,
                "old_penalty_amount": old_penalty_amount,
                "new_penalty_amount": old_penalty_amount,
            }

        updated = await self._habit_repo.update(habit, fields=update_fields)

        # Audit log: фиксируем все force-update финансов клуба.
        # ВАЖНО: audit обязателен — это операция с деньгами.
        self._logger.warning(
            "habit_force_financials_updated",
            extra={
                "admin_id": admin_id,
                "habit_id": habit_id,
                "title": habit.title,
                "chat_id": habit.chat_id,
                "old_price_month": old_price_month,
                "new_price_month": int(updated.price_month),
                "old_penalty_amount": old_penalty_amount,
                "new_penalty_amount": int(updated.penalty_amount),
            },
        )

        return {
            "habit": updated,
            "old_price_month": old_price_month,
            "new_price_month": int(updated.price_month),
            "old_penalty_amount": old_penalty_amount,
            "new_penalty_amount": int(updated.penalty_amount),
        }


def _validate_title(title: str | None) -> None:
    if not isinstance(title, str):
        raise HabitValidationError("title обязателен", code="habit_title_required")
    stripped = title.strip()
    if len(stripped) < 3:
        raise HabitValidationError(
            "title слишком короткий (мин. 3 символа)", code="habit_title_too_short"
        )
    if len(stripped) > 128:
        raise HabitValidationError(
            "title слишком длинный (макс. 128 символов)",
            code="habit_title_too_long",
        )


def _validate_stat_name(name: str | None) -> None:
    if not isinstance(name, str):
        raise HabitValidationError(
            "stat_name обязателен", code="habit_stat_name_required"
        )
    stripped = name.strip()
    if len(stripped) == 0:
        raise HabitValidationError(
            "stat_name не может быть пустым", code="habit_stat_name_empty"
        )
    if len(stripped) > 64:
        raise HabitValidationError(
            "stat_name слишком длинный (макс. 64 символа)",
            code="habit_stat_name_too_long",
        )


def _validate_stat_icon(icon: str | None) -> None:
    # None допустим — означает «нет иконки»
    if icon is None:
        return
    if not isinstance(icon, str):
        raise HabitValidationError(
            "stat_icon должен быть строкой", code="habit_stat_icon_type"
        )
    if len(icon) == 0 or len(icon) > 16:
        raise HabitValidationError(
            "stat_icon: 1–16 символов", code="habit_stat_icon_length"
        )


def _validate_telegram_invite_link(link: str | None) -> None:
    if link is None:
        return
    if not isinstance(link, str):
        raise HabitValidationError(
            "telegram_invite_link должен быть строкой",
            code="habit_invite_link_type",
        )
    if not _TELEGRAM_INVITE_RE.match(link):
        raise HabitValidationError(
            "telegram_invite_link должен быть в формате https://t.me/... или https://t.me/+...",
            code="habit_invite_link_format",
        )


def _validate_timezone(tz: str | None) -> None:
    if not isinstance(tz, str) or not tz:
        raise HabitValidationError(
            "timezone обязателен", code="habit_timezone_required"
        )
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise HabitValidationError(
            f"Неизвестный IANA timezone: {tz}",
            code="habit_timezone_invalid",
        ) from exc


def _validate_checkin_window(start: Any, end: Any) -> None:
    if start is None or end is None:
        raise HabitValidationError(
            "checkin_window_start и checkin_window_end обязательны",
            code="habit_window_required",
        )
    if start >= end:
        raise HabitValidationError(
            "checkin_window_start должен быть < checkin_window_end",
            code="habit_window_order",
        )


def _validate_prices(*, price_month: int, penalty_amount: int) -> None:
    if not isinstance(price_month, int) or price_month <= 0:
        raise HabitValidationError(
            "price_month должен быть положительным INTEGER (копейки)",
            code="habit_price_invalid",
        )
    if not isinstance(penalty_amount, int) or penalty_amount <= 0:
        raise HabitValidationError(
            "penalty_amount должен быть положительным INTEGER (копейки)",
            code="habit_penalty_invalid",
        )


def _validate_stat_gains(
    *, stat_gain_per_checkin: int, stat_loss_per_miss: int
) -> None:
    if not isinstance(stat_gain_per_checkin, int) or stat_gain_per_checkin <= 0:
        raise HabitValidationError(
            "stat_gain_per_checkin должен быть > 0",
            code="habit_stat_gain_invalid",
        )
    if not isinstance(stat_loss_per_miss, int) or stat_loss_per_miss <= 0:
        raise HabitValidationError(
            "stat_loss_per_miss должен быть > 0",
            code="habit_stat_loss_invalid",
        )


def _validate_member_limit(limit: int | None) -> None:
    if limit is None:
        return
    if not isinstance(limit, int) or limit <= 0:
        raise HabitValidationError(
            "member_limit должен быть NULL или положительным INTEGER",
            code="habit_member_limit_invalid",
        )


def _validate_proof_types(proof_types: list[str]) -> None:
    """Массив 1..3 уникальных значений ∈ PROOF_TYPE_VALUES."""
    if not isinstance(proof_types, list):
        raise HabitValidationError(
            "proof_types должен быть массивом",
            code="habit_proof_types_type",
        )
    if len(proof_types) < 1 or len(proof_types) > 3:
        raise HabitValidationError(
            "proof_types должен содержать от 1 до 3 значений",
            code="habit_proof_types_count",
        )
    if len(set(proof_types)) != len(proof_types):
        raise HabitValidationError(
            "proof_types не должны содержать дубликатов",
            code="habit_proof_types_duplicates",
        )
    for pt in proof_types:
        if not isinstance(pt, str) or pt not in PROOF_TYPE_VALUES:
            raise HabitValidationError(
                f"proof_types содержит недопустимое значение: {pt!r}. "
                f"Допустимо: {PROOF_TYPE_VALUES}",
                code="habit_proof_types_invalid",
            )

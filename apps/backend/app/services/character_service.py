"""Сервис глобальных характеристик (Phase 3 v2).

Per layering rule (AGENTS.md): оркестрирует вызовы 3-х репозиториев
из Task 3.2, не вызывает session.commit(), не делает SQL.

4 публичных метода (per Dmitry 21.08.2026):
- increment_on_checkin
- decrement_on_penalty  (NOT-create-on-decrement invariant)
- apply_freeze          (idempotency lives ONLY here)
- get_character         (filter value>0 OR frozen)
"""
from __future__ import annotations

from typing import Any

from app.core.constants import CharacterConfig
from app.core.exceptions import StatDefinitionMissingError
from app.models.user_status import UserStatus
from app.models.user_stats import UserStats
from app.repositories.stat_definition_repository import (
    StatDefinitionRepository,
)
from app.repositories.user_status_repository import UserStatusRepository
from app.repositories.user_stats_repository import UserStatsRepository


class CharacterService:
    def __init__(
        self,
        *,
        user_stats_repo: UserStatsRepository,
        user_status_repo: UserStatusRepository,
        stat_definition_repo: StatDefinitionRepository,
    ) -> None:
        self._user_stats_repo = user_stats_repo
        self._user_status_repo = user_status_repo
        self._stat_definition_repo = stat_definition_repo

    # ─── 1. increment_on_checkin ────────────────────────────

    async def increment_on_checkin(
        self,
        *,
        user_id: int,
        stat_definition_id: str,
        gain: int,
    ) -> int:
        """Returns new value of user_stats.value AFTER increment.

        Шаги (без commit; внутри транзакции caller'а):
        1. (stat, _) = await repo.get_or_create_for_update(...).
           `_` игнорируем: идемпотентность дня — Checkin.created
           (Task 3.4). Сервис работает независимо от этого флага.
        2. Если stat.is_frozen → await repo.unfreeze(stat) — очищает
           ВСЕ 4 frozen-поля (Task 3.2 fix 2).
        3. await repo.increment_value(stat, gain) — pure ORM.
           WARN при gain<=0 (defensive).
        4. await repo.touch_last_checkin(stat) — ВСЕГДА, даже
           если value не изменился из-за gain<=0 (cron защита).
        5. return stat.value.
        """
        stat, _created = await self._user_stats_repo.get_or_create_for_update(
            user_id=user_id,
            stat_definition_id=stat_definition_id,
        )
        if stat.is_frozen:
            await self._user_stats_repo.unfreeze(stat)
        await self._user_stats_repo.increment_value(stat, gain)
        await self._user_stats_repo.touch_last_checkin(stat)
        return stat.value

    # ─── 2. decrement_on_penalty ────────────────────────────

    async def decrement_on_penalty(
        self,
        *,
        user_id: int,
        stat_definition_id: str,
        loss: int,
    ) -> int | None:
        """Returns new value, или None если stat-строки нет.

        ⚠️ КРИТИЧНЫЙ INVARIANT (per Dmitry 21.08.2026):
           Если у юзера НЕТ user_stats-строки для этой stat
           (никогда не чек-инился в stat), а его уже поймали —
           service НЕ СОЗДАЁТ пустую stat-строку (value=0).
           Возвращает None.

           Иначе профиль получает «характеристики только из
           штрафов»: виртуальные строки, которые юзер не заработал.

           Реализация через try_get_for_update — ТОЛЬКО SELECT
           FOR UPDATE, без INSERT. Закреплено тестом
           test_decrement_on_penalty_no_existing_row_returns_none
           как явный invariant.
        """
        stat = await self._user_stats_repo.try_get_for_update(
            user_id=user_id,
            stat_definition_id=stat_definition_id,
        )
        if stat is None:
            # ⚠️ Без create. Нет stat — нечего уменьшать.
            return None
        await self._user_stats_repo.decrement_with_floor(stat, loss)
        return stat.value

    # ─── 3. apply_freeze ──────────────────────────────────────

    async def apply_freeze(
        self,
        *,
        user_id: int,
        stat_definition_id: str,
        reason: str,
    ) -> UserStats | None:
        """Идемпотентный freeze (per Dmitry 21.08.2026 Variant 1).

        Idempotency lives ONLY in this service. Repo.freeze() —
        безусловный «глупый» write-примитив (Task 3.2 решение).

        Шаги:
        1. stat = await repo.try_get_for_update(...) — только
           existing; None → return None (нет stat — нечего).
        2. Если stat.is_frozen=True → return БЕЗ модификации
           (история frozen_at / frozen_reason_text не
           перезаписывается, ни с тем же, ни с другим reason).
        3. Иначе → return await repo.freeze(stat, reason).

        Cron worker (Task 3.5) идёт через repo.iter_for_freeze_cron
        + bulk_freeze напрямую, НЕ через этот метод.
        """
        stat = await self._user_stats_repo.try_get_for_update(
            user_id=user_id,
            stat_definition_id=stat_definition_id,
        )
        if stat is None:
            return None
        if stat.is_frozen:
            # Идемпотентно: НЕ перезаписываем frozen_at / reason.
            return stat
        return await self._user_stats_repo.freeze(stat, reason)

    # ─── 4. get_character ────────────────────────────────────

    async def get_character(self, *, user_id: int) -> dict[str, Any]:
        """Payload для GET /api/v1/character/me (Task 3.6 endpoint).

        Правила фильтрации stats[] (per Dmitry 21.08.2026):
        - ВКЛЮЧАТЬ stat если value >= MIN_STAT_VALUE_TO_SHOW (= 1);
        - ВКЛЮЧАТЬ stat если is_frozen=true (даже при value=0);
        - ИСКЛЮЧАТЬ stat если value < MIN_STAT_VALUE_TO_SHOW И is_frozen=false.

        Total считается по ВСЕМ stats (включая отфильтрованные).
        Статус ВСЕГДА существует — min_threshold=0 «На старте»
        покрывает total=0.

        ⚠️ Per Dmitry 21.08.2026:
        - StatDefinitionMissingError: если StatDefinition отсутствует
          для видимой stat — это нарушение referential integrity
          (FK должен защищать). НЕ маскируем пустыми строками,
          иначе скроем расхождение данных от юзера и от логов.
          Исключение делает проблему наблюдаемой для вызывающего
          API-слоя (status_code=500 зафиксирован в
          StatDefinitionMissingError.status_code; фактический HTTP
          mapping — Task 3.6).
        - last_checkin_at НЕ сериализуется здесь (datetime | None);
          сериализация в ISO — задача API-schema на Task 3.6.
        """
        all_stats = await self._user_stats_repo.list_for_user(user_id)
        visible = [
            s for s in all_stats
            if s.value >= CharacterConfig.MIN_STAT_VALUE_TO_SHOW or s.is_frozen
        ]
        total_value = sum(s.value for s in all_stats)

        statuses = await self._user_status_repo.list_all_ordered()
        current_status, next_threshold, next_status_name = (
            self._calculate_status(total_value, statuses)
        )

        # Enrich visible stats. ⚠️ StatDefinition обязателен — иначе
        # поднимаем StatDefinitionMissingError с extras для observability.
        enriched: list[dict[str, Any]] = []
        for vs in visible:
            sd = await self._stat_definition_repo.get_by_id(
                vs.stat_definition_id
            )
            if sd is None:
                raise StatDefinitionMissingError(
                    stat_definition_id=vs.stat_definition_id,
                    user_stats_id=vs.id,
                    user_id=user_id,
                )
            enriched.append(
                {
                    "stat_id": vs.id,
                    "stat_definition_id": vs.stat_definition_id,
                    "value": vs.value,
                    "is_frozen": vs.is_frozen,
                    "frozen_reason_text": vs.frozen_reason_text,
                    # ⚠️ datetime | None — сериализация НЕ здесь (Task 3.6).
                    "last_checkin_at": vs.last_checkin_at,
                    "stat_slug": sd.slug,
                    "stat_name": sd.name,
                    "stat_icon": sd.icon,
                }
            )

        return {
            "total_value": total_value,
            "status": {
                "name": current_status["name"],
                "icon": current_status["icon"],
                "next_threshold": next_threshold,
                "next_status": next_status_name,
            },
            "stats": enriched,
        }

    @staticmethod
    def _calculate_status(
        total_value: int,
        statuses: list[UserStatus],
    ) -> tuple[dict[str, Any], int | None, str | None]:
        """Returns (current {name, icon}, next_threshold, next_status_name).

        statuses ожидается отсортированным по sort_order ASC (гарантия
        UserStatusRepository.list_all_ordered).

        current = последняя строка с min_threshold <= total_value.
        next = первая строка с min_threshold > total_value, или None
        если уже на максимальной ступени.

        Defensive fallback: пустой справочник → «На старте» (per
        Dmitry 21.08.2026 — оставляем явно покрытым тестом
        test_get_character_empty_status_catalog_uses_start_fallback,
        но на проде миграция 019 засеивает 5 строк).
        """
        if not statuses:
            return ({"name": "На старте", "icon": "🐣"}, None, None)

        current_idx = 0
        for i, s in enumerate(statuses):
            if s.min_threshold <= total_value:
                current_idx = i
            else:
                break
        current = statuses[current_idx]

        if current_idx + 1 < len(statuses):
            next_s = statuses[current_idx + 1]
            return (
                {"name": current.status_name, "icon": current.icon},
                next_s.min_threshold,
                next_s.status_name,
            )
        return ({"name": current.status_name, "icon": current.icon}, None, None)

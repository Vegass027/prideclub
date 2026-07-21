from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SeasonStatus, TransactionType
from app.core.logging import get_logger
from app.models.auxiliary import SeasonPrizeRule
from app.models.habit import Habit
from app.models.season import Season, SeasonStats
from app.models.transaction import Transaction


# Basis points: 10000 = 100.00%. Допускает доли процента до 0.01%.
# Все money-арифметики с процентами — только int, никакого float (см. AGENTS.md).
BASIS_POINTS_TOTAL = 10_000


def _to_basis_points(percentage_bp: int) -> int:
    """Контракт входа: percentage_bp — int в диапазоне [0, 10_000] (0%..100%)."""
    if not 0 <= percentage_bp <= BASIS_POINTS_TOTAL:
        raise ValueError(
            f"percentage_bp={percentage_bp} вне [0, {BASIS_POINTS_TOTAL}]"
        )
    return percentage_bp


class SeasonService:
    """Сезоны и распределение призового фонда (см. docs/06-data-model §7).

    Контракт правил (`prize_rules_snapshot`):
      `{"rules": [{"metric": str, "rank_from": int, "rank_to": int,
                   "percentage_bp": int}, ...]}`
    где `percentage_bp` — basis points (10000 = 100%, 3333 = 33.33%).
    Все арифметики — целочисленные, чтобы распределение было бит-в-бит
    воспроизводимым и не зависело от float-представления.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._logger = get_logger("season_service")

    async def start_season(
        self, *, habit: Habit, starts_at, ends_at, rules: list[dict]
    ) -> Season:
        validate_prize_rules(rules)
        season = Season(
            id=str(uuid4()),
            habit_id=str(habit.id),
            starts_at=starts_at,
            ends_at=ends_at,
            prize_rules_snapshot={"rules": rules},
        )
        self._session.add(season)
        await self._session.flush()
        return season

    async def close_season(self, *, season_id: str) -> dict:
        """Закрытие сезона и распределение призового фонда (атомарно).

        Идемпотентность: SELECT ... FOR UPDATE на строке Season.
        Конкурентный вызов (cron + ручной retry, или два worker'а одновременно)
        сериализуется на этой блокировке — второй worker ждёт, читает
        уже CLOSED, выходит с distributed=0.

        Без FOR UPDATE оба worker'а прочитали бы ACTIVE, оба создали бы
        Transaction'ы с PRIZE, итого участники получили бы приз дважды
        → реальные потери денег.
        """
        season_obj = await self._session.get(Season, season_id, with_for_update=True)
        if season_obj is None:
            raise ValueError(f"Season {season_id} not found")
        if season_obj.status != SeasonStatus.ACTIVE:
            return {"distributed": 0}

        rules = season_obj.prize_rules_snapshot.get("rules", []) if season_obj.prize_rules_snapshot else []
        validate_prize_rules(rules)

        distributed = 0
        for rule in rules:
            metric = rule["metric"]
            rank_from = rule["rank_from"]
            rank_to = rule["rank_to"]
            percentage_bp = _to_basis_points(int(rule["percentage_bp"]))

            ranked = await self._rank_by_metric(
                season_id, metric, rank_from=rank_from, rank_to=rank_to
            )
            if not ranked:
                continue

            # Целочисленная арифметика: prize_pool * percent_bp // 10_000.
            # Никакого float — точно воспроизводимо.
            per_member_pool = (
                season_obj.prize_pool * percentage_bp // BASIS_POINTS_TOTAL
            )
            share = per_member_pool // len(ranked)

            for entry in ranked:
                tx = Transaction(
                    id=str(uuid4()),
                    user_id=entry["user_id"],
                    type=TransactionType.PRIZE.value,
                    amount=share,
                    related_membership_id=entry["membership_id"],
                )
                self._session.add(tx)
                distributed += share

        season_obj.status = SeasonStatus.CLOSED
        await self._session.flush()
        self._logger.info(
            "season_closed",
            extra={"season_id": str(season_obj.id), "distributed": distributed},
        )
        return {"distributed": distributed}

    async def _rank_by_metric(
        self, season_id: str, metric: str, *, rank_from: int, rank_to: int
    ) -> list[dict]:
        """Минимальная реализация: streak и catches. Shame — отдельная таблица.

        Возвращает список dict'ов с `membership_id` и `user_id` (нужно для
        записи `Transaction` в `close_season`).
        """
        if metric in ("streak", "catches"):
            from app.models.membership import Membership

            stat_field = (
                SeasonStats.streak_days
                if metric == "streak"
                else SeasonStats.total_penalties_caught
            )
            rows = (
                await self._session.execute(
                    select(
                        SeasonStats.membership_id,
                        Membership.user_id,
                        stat_field.label("metric_value"),
                    )
                    .join(Membership, Membership.id == SeasonStats.membership_id)
                    .where(SeasonStats.season_id == season_id)
                    .order_by(stat_field.desc())
                )
            ).all()

            sorted_rows = sorted(rows, key=lambda r: r.metric_value or 0, reverse=True)
            sliced = sorted_rows[rank_from - 1 : rank_to]
            if not sliced:
                return []

            return [
                {
                    "membership_id": str(r.membership_id),
                    "user_id": int(r.user_id),
                    "metric_value": int(r.metric_value or 0),
                }
                for r in sliced
        ]
        return []


def validate_prize_rules(rules: Iterable[dict]) -> None:
    """Валидирует правила распределения призового фонда (целочисленно).

    Контракт:
    - Каждое правило содержит `percentage_bp: int` в `[0, 10_000]`.
    - Сумма `percentage_bp` по всем правилам = ровно `10_000` (100%).
    - rank_from >= 1, rank_from <= rank_to, без перекрытий внутри одной метрики.
    - Метрика — просто тег группировки ('streak', 'catches', и т.п.).
    """
    from app.core.exceptions import InvalidPrizeRulesError

    seen_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    total_bp = 0
    for rule in rules:
        rf, rt = rule["rank_from"], rule["rank_to"]
        if rf < 1 or rf > rt:
            raise InvalidPrizeRulesError(f"invalid range {rf}-{rt}")

        # percentage_bp — int в basis points; не пускаем float/Decimal в арифметику.
        try:
            bp = _to_basis_points(int(rule["percentage_bp"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidPrizeRulesError(
                f"percentage_bp отсутствует или не int: {rule!r}"
            ) from exc

        metric = rule["metric"]
        for prev_rf, prev_rt in seen_ranges[metric]:
            if rf <= prev_rt and prev_rf <= rt:
                raise InvalidPrizeRulesError(
                    f"overlapping ranges in '{metric}': [{prev_rf},{prev_rt}] and [{rf},{rt}]"
                )
        seen_ranges[metric].append((rf, rt))
        total_bp += bp

    if total_bp != BASIS_POINTS_TOTAL:
        raise InvalidPrizeRulesError(
            f"total percentage_bp = {total_bp}, expected {BASIS_POINTS_TOTAL} (100%)"
        )
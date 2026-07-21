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


class SeasonService:
    """Сезоны и распределение призового фонда (см. docs/06-data-model §7)."""

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
        season = await self._session.execute(
            select(Season).where(Season.id == season_id)
        )
        season_obj = season.scalar_one()
        if season_obj.status != SeasonStatus.ACTIVE:
            return {"distributed": 0}

        rules = season_obj.prize_rules_snapshot.get("rules", []) if season_obj.prize_rules_snapshot else []
        validate_prize_rules(rules)

        distributed = 0
        for rule in rules:
            metric = rule["metric"]
            rank_from = rule["rank_from"]
            rank_to = rule["rank_to"]
            percentage = float(rule["percentage"])

            ranked = await self._rank_by_metric(
                season_id, metric, rank_from=rank_from, rank_to=rank_to
            )
            if not ranked:
                continue

            per_member_pool = int(season_obj.prize_pool * percentage / 100)
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
        # Минимальная реализация: streak и catches. Shame — отдельная таблица.
        if metric in ("streak", "catches"):
            stat_field = (
                SeasonStats.streak_days
                if metric == "streak"
                else SeasonStats.total_penalties_caught
            )
            rows = (
                await self._session.execute(
                    select(
                        SeasonStats.membership_id,
                        stat_field.label("metric_value"),
                    )
                    .where(SeasonStats.season_id == season_id)
                    .order_by(stat_field.desc())
                )
            ).all()

            from sqlalchemy import desc

            sorted_rows = sorted(rows, key=lambda r: r.metric_value or 0, reverse=True)
            sliced = sorted_rows[rank_from - 1 : rank_to]
            if not sliced:
                return []

            membership_ids = [str(r.membership_id) for r in sliced]
            user_rows = (
                await self._session.execute(
                    select(SeasonStats.membership_id, SeasonStats.season_id)
                    .where(SeasonStats.season_id == season_id)
                )
            ).all()
            return [
                {
                    "membership_id": str(r.membership_id),
                    "user_id": None,
                    "metric_value": int(r.metric_value or 0),
                }
                for r in sliced
            ]
        return []


def validate_prize_rules(rules: Iterable[dict]) -> None:
    """Валидирует правила распределения призового фонда.

    Контракт (docs/06-data-model.md §7):
    - Сумма ВСЕХ percentage по всем metrics = 100% (распределение фонда).
    - Метрика — это просто тег группировки ('streak', 'catches', и т.п.).
    - rank_from >= 1, rank_from <= rank_to, без перекрытий внутри одной метрики.
    """
    from app.core.exceptions import InvalidPrizeRulesError

    seen_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    total_pct = 0.0
    for rule in rules:
        rf, rt = rule["rank_from"], rule["rank_to"]
        if rf < 1 or rf > rt:
            raise InvalidPrizeRulesError(f"invalid range {rf}-{rt}")
        metric = rule["metric"]
        for prev_rf, prev_rt in seen_ranges[metric]:
            if rf <= prev_rt and prev_rf <= rt:
                raise InvalidPrizeRulesError(
                    f"overlapping ranges in '{metric}': [{prev_rf},{prev_rt}] and [{rf},{rt}]"
                )
        seen_ranges[metric].append((rf, rt))
        total_pct += float(rule["percentage"])

    if abs(total_pct - 100.0) > 0.01:
        raise InvalidPrizeRulesError(
            f"total percentage = {total_pct}, expected 100"
        )
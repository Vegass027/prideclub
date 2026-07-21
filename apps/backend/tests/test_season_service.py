from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from app.core.constants import SeasonStatus, TransactionType
from app.core.exceptions import InvalidPrizeRulesError
from app.models.season import Season
from app.services.season_service import (
    BASIS_POINTS_TOTAL,
    SeasonService,
    validate_prize_rules,
)


def test_validate_prize_rules_ok_basis_points() -> None:
    """Сумма percentage_bp по всем метрикам = 10_000 (100%)."""
    rules = [
        {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage_bp": 4000},
        {"metric": "streak", "rank_from": 2, "rank_to": 2, "percentage_bp": 2000},
        {"metric": "streak", "rank_from": 3, "rank_to": 3, "percentage_bp": 1000},
        {"metric": "catches", "rank_from": 1, "rank_to": 1, "percentage_bp": 2000},
        {"metric": "catches", "rank_from": 2, "rank_to": 2, "percentage_bp": 1000},
    ]
    validate_prize_rules(rules)  # не должно бросить


def test_validate_prize_rules_must_sum_10_000_bp() -> None:
    """Сумма ≠ 10_000 → InvalidPrizeRulesError. Проверка с totals меньше 100%."""
    rules = [
        {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage_bp": 3000},
        {"metric": "streak", "rank_from": 2, "rank_to": 2, "percentage_bp": 2000},
    ]
    with pytest.raises(InvalidPrizeRulesError):
        validate_prize_rules(rules)


def test_validate_prize_rules_invalid_range() -> None:
    rules = [
        {"metric": "streak", "rank_from": 5, "rank_to": 2, "percentage_bp": 10_000},
    ]
    with pytest.raises(InvalidPrizeRulesError):
        validate_prize_rules(rules)


def test_validate_prize_rules_rejects_float_percentage_bp() -> None:
    """percentage_bp должен быть int — float сначала конвертируется через int(),
    и тогда 33.33% → 33 bp = 0.33%, теряя точность. Защита от тихого float-входа."""
    rules = [
        {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage_bp": 33.33},
    ]
    with pytest.raises(InvalidPrizeRulesError):
        validate_prize_rules(rules)


def test_validate_prize_rules_rejects_out_of_range_bp() -> None:
    """percentage_bp > 10_000 (больше 100%) или < 0 → ValueError."""
    rules = [
        {"metric": "streak", "rank_from": 1, "rank_to": 1, "percentage_bp": 10_001},
    ]
    with pytest.raises(InvalidPrizeRulesError):
        validate_prize_rules(rules)


# --- Close season: целочисленная арифметика без float ---


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value


class _FakeExecuteResult:
    def __init__(self, rows: list = None) -> None:
        self._rows = rows or []

    def all(self) -> list:
        return self._rows


class _FakeSession:
    """Достаточно для close_season: SELECT season, ADD transaction, FLUSH."""

    def __init__(self) -> None:
        self.added: list = []
        self.flushed = 0
        # Фиксируем вызовы session.get(..., with_for_update=True) для
        # проверки контракта блокировки в U7-тестах.
        self.get_calls: list[tuple[str, bool]] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def get(self, model: Any, pk: Any, *, with_for_update: bool = False) -> Any:
        # Записываем вызовы с флагом FOR UPDATE для проверки контракта.
        self.get_calls.append((str(pk), with_for_update))
        return self._season

    async def execute(self, stmt: Any) -> Any:
        # season lookup через execute оставлен для обратной совместимости,
        # но новый код должен использовать session.get с with_for_update.
        if "seasons" in str(stmt).lower():
            return _FakeResult(self._season)
        # season stats ranking
        if "season_stats" in str(stmt).lower():
            return _FakeExecuteResult(
                [
                    type("R", (), {
                        "membership_id": "m1",
                        "user_id": 100,
                        "metric_value": 10,
                    })(),
                ]
            )
        return _FakeExecuteResult()


@pytest.mark.asyncio
async def test_close_season_uses_int_arithmetic_not_float() -> None:
    """`share = prize_pool * bp // 10_000 // len(ranked)` — целочисленно.

    Контрактная проверка через явную арифметику:
      prize_pool = 10_001 коп., правило 3333bp (33.33%), 1 победитель.
      (10_001 * 3333) // 10_000 = 3333 (округление вниз при делении).
    Это значение СОВПАДАЕТ со старым float-путём в этом частном случае,
    поэтому проверяем *точное значение* + источник-чистоту от float.
    Ценность теста в `test_season_service_source_has_no_float_arithmetic`.
    """
    season = Season(
        id=str(uuid4()),
        habit_id="habit-1",
        starts_at=date(2026, 1, 1),
        ends_at=date(2026, 2, 1),
        status=SeasonStatus.ACTIVE,
        prize_pool=10_001,
        prize_rules_snapshot={
            "rules": [
                {
                    "metric": "streak",
                    "rank_from": 1,
                    "rank_to": 1,
                    "percentage_bp": 3333,
                },
                {
                    "metric": "catches",
                    "rank_from": 1,
                    "rank_to": 1,
                    "percentage_bp": BASIS_POINTS_TOTAL - 3333,
                },
            ]
        },
    )

    session = _FakeSession()
    session._season = season
    service = SeasonService(session)  # type: ignore[arg-type]

    await service.close_season(season_id=str(season.id))

    prize_txs = [
        t for t in session.added
        if getattr(t, "type", None) == TransactionType.PRIZE.value
    ]
    assert len(prize_txs) == 2, "одна транзакция на каждое правило"
    streak_tx = prize_txs[0]
    # (10_001 * 3333) // 10_000 = 3333 (точный int, не float).
    assert streak_tx.amount == 3333, (
        "int-арифметика basis_points; регресс укажет на float или P/100"
    )


def test_season_service_source_has_no_float_arithmetic() -> None:
    """Защита от регресса: в исходниках сезон-сервиса не должно быть float.

    Скилл и AGENTS.md запрещают float в money-арифметике. Если кто-то добавит
    `float(...)` обратно при bugfix — тест упадёт с указанием строки.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "services" / "season_service.py"
    text = src.read_text(encoding="utf-8")
    # Комментарии с упоминанием float — ок. Проверяем код, а не докстринг.
    bad_lines = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.split("#", 1)[0]
        if "float(" in stripped:
            bad_lines.append(f"{i}: {line}")
    assert not bad_lines, (
        "float() в season_service.py недопустим:\n" + "\n".join(bad_lines)
    )


@pytest.mark.asyncio
async def test_close_season_idempotent_on_second_call() -> None:
    """Повторный close на закрытом сезоне — no-op distributed=0."""
    season = Season(
        id=str(uuid4()),
        habit_id="habit-1",
        starts_at=date(2026, 1, 1),
        ends_at=date(2026, 2, 1),
        status=SeasonStatus.CLOSED,  # уже закрыт
        prize_pool=0,
        prize_rules_snapshot={"rules": []},
    )

    session = _FakeSession()
    session._season = season
    service = SeasonService(session)  # type: ignore[arg-type]

    result = await service.close_season(season_id=str(season.id))
    assert result == {"distributed": 0}
    assert session.added == []  # ничего не добавлено


# ---------------------------------------------------------------------------
# U7: идемпотентность close_season под конкурентным запуском.
# Контракт: SELECT ... FOR UPDATE на Season блокирует параллельный worker.
# Без блокировки два worker'а прочитали бы ACTIVE → оба создали бы PRIZE-транзакции
# → участники получили бы приз дважды (реальные потери денег).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_season_acquires_row_lock_on_season() -> None:
    """close_season обязан идти через session.get(Season, pk, with_for_update=True).

    Без with_for_update=True параллельный worker прочитает status=ACTIVE,
    не дождавшись нашего flush'а, и начислит приз дважды.
    """
    season = Season(
        id=str(uuid4()),
        habit_id="habit-1",
        starts_at=date(2026, 1, 1),
        ends_at=date(2026, 2, 1),
        status=SeasonStatus.ACTIVE,
        prize_pool=10_000,
        prize_rules_snapshot={
            "rules": [
                {
                    "metric": "streak",
                    "rank_from": 1,
                    "rank_to": 1,
                    "percentage_bp": BASIS_POINTS_TOTAL,
                },
            ]
        },
    )
    session = _FakeSession()
    session._season = season
    service = SeasonService(session)  # type: ignore[arg-type]

    await service.close_season(season_id=str(season.id))

    assert any(
        pk == str(season.id) and with_fu is True
        for pk, with_fu in session.get_calls
    ), (
        f"close_season должен вызвать session.get(Season, pk, with_for_update=True). "
        f"Получили вызовы: {session.get_calls}. "
        f"Без FOR UPDATE = гонка между worker'ами = двойное начисление приза"
    )


@pytest.mark.asyncio
async def test_close_season_rejects_unknown_season() -> None:
    """Несуществующий season_id → ValueError (а не silent return 0).

    Раньше код делал `scalar_one()` — падал с NoResultFound на None.
    Теперь через `session.get` мы явно возвращаем None и сами решаем —
    ValueError, потому что caller обязан проверить ID до вызова.
    """
    session = _FakeSession()

    async def _get_returning_none(*args, **kwargs):
        return None

    session.get = _get_returning_none  # type: ignore[assignment]
    service = SeasonService(session)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="not found"):
        await service.close_season(season_id="nonexistent-season")


@pytest.mark.asyncio
async def test_close_season_serializes_parallel_workers() -> None:
    """Два параллельных worker'а → только один распределит приз.

    Имитация гонки: два `close_season` стартуют одновременно. В реальной БД
    FOR UPDATE сериализует их: первый залочил Season, второй ждёт. Когда
    второй входит — статус уже CLOSED → distributed=0.

    Здесь мы имитируем это вручную: первый вызов успешно распределяет и
    переводит в CLOSED. Второй вызов видит CLOSED → 0. Контракт защиты
    проверяется через `_FakeSession.get_calls` и финальное состояние season.
    """
    season = Season(
        id=str(uuid4()),
        habit_id="habit-1",
        starts_at=date(2026, 1, 1),
        ends_at=date(2026, 2, 1),
        status=SeasonStatus.ACTIVE,
        prize_pool=10_000,
        prize_rules_snapshot={
            "rules": [
                {
                    "metric": "streak",
                    "rank_from": 1,
                    "rank_to": 1,
                    "percentage_bp": BASIS_POINTS_TOTAL,
                },
            ]
        },
    )
    session = _FakeSession()
    session._season = season
    service = SeasonService(session)  # type: ignore[arg-type]

    # Worker #1: заходит под FOR UPDATE, видит ACTIVE, распределяет,
    # переводит в CLOSED, flush'ит.
    r1 = await service.close_season(season_id=str(season.id))
    assert r1["distributed"] == 10_000
    assert season.status == SeasonStatus.CLOSED

    # Worker #2: стартует "после" worker #1. В реальной БД FOR UPDATE дал
    # бы ему подождать, и он бы прочитал уже CLOSED. В фейке просто
    # перечитываем season — он уже CLOSED → distributed=0.
    r2 = await service.close_season(season_id=str(season.id))
    assert r2 == {"distributed": 0}, (
        f"повторный close на CLOSED-сезоне должен возвращать 0; получили {r2}. "
        f"Если != 0 — регресс, приз начислится дважды"
    )

    # Проверяем что PRIZE-транзакция создана РОВНО ОДИН РАЗ.
    prize_txs = [
        t for t in session.added
        if getattr(t, "type", None) == TransactionType.PRIZE.value
    ]
    assert len(prize_txs) == 1, (
        f"должна быть ровно 1 PRIZE-транзакция; получили {len(prize_txs)}. "
        f"Если 2 — регресс: оба worker'а начислили"
    )


@pytest.mark.asyncio
async def test_close_season_emits_for_update_before_flush() -> None:
    """Жёсткий порядок: сначала FOR UPDATE (get), затем распределение, потом flush.

    Если порядок нарушен (например, distribute до lock) — между ними другой
    worker может прочитать ACTIVE → race.
    """
    season = Season(
        id=str(uuid4()),
        habit_id="habit-1",
        starts_at=date(2026, 1, 1),
        ends_at=date(2026, 2, 1),
        status=SeasonStatus.ACTIVE,
        prize_pool=10_000,
        prize_rules_snapshot={
            "rules": [
                {
                    "metric": "streak",
                    "rank_from": 1,
                    "rank_to": 1,
                    "percentage_bp": BASIS_POINTS_TOTAL,
                },
            ]
        },
    )
    session = _FakeSession()
    session._season = season
    service = SeasonService(session)  # type: ignore[arg-type]

    await service.close_season(season_id=str(season.id))

    # Конкретно: сразу при входе в close_season — get с with_for_update=True.
    first_get = session.get_calls[0]
    assert first_get == (str(season.id), True), (
        f"первый вызов должен быть session.get(Season, pk, with_for_update=True); "
        f"получили {first_get}. Без FOR UPDATE = гонка между worker'ами"
    )
    # flush был вызван в конце распределения (после status=CLOSED).
    assert session.flushed >= 1, (
        f"после распределения должен быть flush; получили flushed={session.flushed}"
    )

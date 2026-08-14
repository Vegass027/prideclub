"""Unit-тесты для PenaltyRepository.

Pravki-paused-window-open-2026-08-14: фикс лжи в TodayPage ("Штраф уже
списан" для ЛЮБОГО missed). Метод `amount_for_today` — новая единица
SQL-логики, нужен явный тест на контракт (сумма считается правильно,
0 если записей нет). Интеграционные тесты с реальной Postgres
запускаются через docker-compose (pre-existing baseline); здесь —
быстрые unit-тесты на FakePenaltyRepo, имитирующую ту же агрегацию.

Если в проде FakePenaltyRepo.amount_for_today расходится с настоящим
PenaltyRepository.amount_for_today — этот тест поймает drift.
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from app.models.penalty import Penalty
from tests.fakes import FakePenaltyRepo


def _make_penalty(*, membership_id: str, day: date, amount: int, reason: str = "caught") -> Penalty:
    """Helper — конструирует Penalty для теста без полной модели."""
    return Penalty(
        id=str(uuid4()),
        membership_id=membership_id,
        catcher_membership_id=None,
        amount=amount,
        fund_share=amount,
        catcher_bonus_points=0,
        reason=reason,
        date=day,
        bonus_applied=False,
    )


@pytest.mark.asyncio
async def test_amount_for_today_returns_zero_when_no_penalties() -> None:
    """Пустой репозиторий → amount_for_today = 0.

    Граничный кейс для TodayPage при status="missed" без реального
    штрафа (apply_window_expired вернул None из-за deposit=0).
    UI должен показать "штраф не списан", а не "штраф списан".
    """
    repo = FakePenaltyRepo()
    assert await repo.amount_for_today(
        membership_id="mem-1", club_date=date(2026, 8, 14)
    ) == 0


@pytest.mark.asyncio
async def test_amount_for_today_sums_only_same_membership_and_date() -> None:
    """Суммирует penalty только для (membership_id, club_date).

    Несколько penalty для одного юзера в разные дни + чужие penalty
    в тот же день — должно вернуть сумму только своего юзера за свой день.
    """
    repo = FakePenaltyRepo()

    # Свои penalty за разные дни
    repo.add(_make_penalty(membership_id="mem-self", day=date(2026, 8, 14), amount=12500))
    repo.add(_make_penalty(membership_id="mem-self", day=date(2026, 8, 13), amount=99999))
    # Чужие penalty в тот же день
    repo.add(_make_penalty(membership_id="mem-other", day=date(2026, 8, 14), amount=50000))
    # Свои penalty с разным reason за тот же день — должны суммироваться
    repo.add(
        _make_penalty(
            membership_id="mem-self",
            day=date(2026, 8, 14),
            amount=12500,
            reason="window_closed_no_catch",
        )
    )

    total = await repo.amount_for_today(
        membership_id="mem-self", club_date=date(2026, 8, 14)
    )
    # 12500 (caught) + 12500 (window_closed_no_catch) = 25000
    assert total == 25000, (
        f"Ожидали 25000 (= 12500+12500 для mem-self за 14.08), получили {total}. "
        f"Метод НЕ должен включать penalty других юзеров или другие дни."
    )


@pytest.mark.asyncio
async def test_amount_for_today_returns_zero_for_other_dates() -> None:
    """Запрос на club_date без записей за этот день → 0,
    даже если за другие дни есть penalty.
    """
    repo = FakePenaltyRepo()
    repo.add(_make_penalty(membership_id="mem-1", day=date(2026, 8, 13), amount=25000))

    assert await repo.amount_for_today(
        membership_id="mem-1", club_date=date(2026, 8, 14)
    ) == 0

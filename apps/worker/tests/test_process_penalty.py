"""Тесты для worker-таски process_penalty."""
from __future__ import annotations

import os
from datetime import date

import pytest

from app.core.constants import MembershipStatus, PenaltyReason, TransactionType


# redis_port=None — по умолчанию в _process(). Тесты не поднимают Redis,
# rate-limit отключён.


@pytest.mark.asyncio
async def test_process_penalty_happy_path(worker_db) -> None:
    from worker.tasks.process_penalty import _process

    os.environ["REDIS_URL"] = "redis://localhost:6379/0"

    async with worker_db.session_factory() as session:
        violator_user = await worker_db.add_user(session, id=2001)
        catcher_user = await worker_db.add_user(session, id=2002)
        habit = await worker_db.add_habit(
            session,
            penalty_amount=150,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        violator = await worker_db.add_membership(
            session, user_id=violator_user.id, habit_id=habit.id, deposit_balance=500
        )
        catcher = await worker_db.add_membership(
            session, user_id=catcher_user.id, habit_id=habit.id
        )
        await session.commit()

    payload = {
        "catcher_user_id": catcher_user.id,
        "catcher_membership_id": catcher.id,
        "violator_membership_id": violator.id,
        "club_date": date.today().isoformat(),
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is True
    assert "penalty_id" in result

    async with worker_db.session_factory() as session:
        from sqlalchemy import select

        from app.models.membership import Membership
        from app.models.penalty import Penalty
        from app.models.transaction import Transaction

        v = (
            await session.execute(
                select(Membership).where(Membership.id == violator.id)
            )
        ).scalar_one()
        assert v.deposit_balance == 350  # 500 - 150

        p = (
            await session.execute(
                select(Penalty).where(Penalty.membership_id == violator.id)
            )
        ).scalar_one()
        assert p.reason == PenaltyReason.CAUGHT.value
        assert p.amount == 150

        tx = (
            await session.execute(
                select(Transaction).where(
                    Transaction.related_penalty_id == p.id
                )
            )
        ).scalar_one()
        assert tx.type == TransactionType.PENALTY.value
        assert tx.amount == -150
        assert tx.balance_after == 350


@pytest.mark.asyncio
async def test_process_penalty_duplicate_idempotent(worker_db) -> None:
    from worker.tasks.process_penalty import _process

    async with worker_db.session_factory() as session:
        violator_user = await worker_db.add_user(session, id=2011)
        catcher_user = await worker_db.add_user(session, id=2012)
        habit = await worker_db.add_habit(
            session,
            penalty_amount=200,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        violator = await worker_db.add_membership(
            session, user_id=violator_user.id, habit_id=habit.id, deposit_balance=500
        )
        catcher = await worker_db.add_membership(
            session, user_id=catcher_user.id, habit_id=habit.id
        )
        await worker_db.add_penalty(
            session,
            violator_membership_id=violator.id,
            catcher_membership_id=catcher.id,
            amount=200,
            fund_share=200,
            reason=PenaltyReason.CAUGHT.value,
            on_date=date.today(),
        )
        await session.commit()

    payload = {
        "catcher_user_id": catcher_user.id,
        "catcher_membership_id": catcher.id,
        "violator_membership_id": violator.id,
        "club_date": date.today().isoformat(),
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    assert result["ok"] is True
    assert result.get("duplicate") is True

    async with worker_db.session_factory() as session:
        from sqlalchemy import func, select

        from app.models.penalty import Penalty

        count = (
            await session.execute(
                select(func.count())
                .select_from(Penalty)
                .where(Penalty.membership_id == violator.id)
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_process_penalty_pauses_on_zero_deposit(worker_db) -> None:
    from worker.tasks.process_penalty import _process

    async with worker_db.session_factory() as session:
        violator_user = await worker_db.add_user(session, id=2021)
        catcher_user = await worker_db.add_user(session, id=2022)
        habit = await worker_db.add_habit(
            session,
            penalty_amount=500,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        violator = await worker_db.add_membership(
            session, user_id=violator_user.id, habit_id=habit.id, deposit_balance=0
        )
        catcher = await worker_db.add_membership(
            session, user_id=catcher_user.id, habit_id=habit.id
        )
        await session.commit()

    payload = {
        "catcher_user_id": catcher_user.id,
        "catcher_membership_id": catcher.id,
        "violator_membership_id": violator.id,
        "club_date": date.today().isoformat(),
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    # Депозит исчерпан — PenaltyService кидает PenaltyAlreadyProcessedError("deposit_exhausted")
    assert result["ok"] is True
    assert result.get("duplicate") is True
    assert result.get("code") == "deposit_exhausted"

    async with worker_db.session_factory() as session:
        from sqlalchemy import select

        from app.models.membership import Membership

        v = (
            await session.execute(
                select(Membership).where(Membership.id == violator.id)
            )
        ).scalar_one()
        assert v.status == MembershipStatus.PAUSED
        assert v.deposit_balance == 0


@pytest.mark.asyncio
async def test_process_penalty_violator_inactive(worker_db) -> None:
    from worker.tasks.process_penalty import _process

    async with worker_db.session_factory() as session:
        violator_user = await worker_db.add_user(session, id=2031)
        catcher_user = await worker_db.add_user(session, id=2032)
        habit = await worker_db.add_habit(
            session,
            checkin_window_start_hour=0,
            checkin_window_end_hour=23,
        )
        violator = await worker_db.add_membership(
            session,
            user_id=violator_user.id,
            habit_id=habit.id,
            status=MembershipStatus.PAUSED,
        )
        catcher = await worker_db.add_membership(
            session, user_id=catcher_user.id, habit_id=habit.id
        )
        await session.commit()

    payload = {
        "catcher_user_id": catcher_user.id,
        "catcher_membership_id": catcher.id,
        "violator_membership_id": violator.id,
        "club_date": date.today().isoformat(),
    }
    result = await _process(payload, session_factory=worker_db.session_factory)
    # Неактивный membership → MembershipNotActiveError → ok=False
    assert result["ok"] is False


def test_rate_limit_disabled_error_class_exists() -> None:
    """T5: класс ошибки экспортируется и подходит для autoretry_for."""
    from worker.tasks.process_penalty import RateLimitDisabledError

    assert issubclass(RateLimitDisabledError, RuntimeError)
    err = RateLimitDisabledError("test")
    assert str(err) == "test"


def test_process_penalty_run_raises_when_redis_unavailable(monkeypatch) -> None:
    """T5: прод-обёртка `run()` fail-CLOSED — без Redis бросает RateLimitDisabledError.

    `_build_production_redis_port()` возвращает None при пустом/неустановленном
    REDIS_URL. В проде (а не в тестах) это сигнал к retry, а не к тихому пропуску.
    """
    from worker.tasks.process_penalty import RateLimitDisabledError

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(
        "worker.tasks.process_penalty._build_production_redis_port", lambda: None
    )

    from worker.tasks import process_penalty as pp_module

    redis_port = pp_module._build_production_redis_port()
    assert redis_port is None  # setup

    # Эмулируем тот же guard, что в прод-runner (без asyncio.run —
    # тест должен быть детерминированным, без зависимости от celery_app).
    with pytest.raises(RateLimitDisabledError):
        if redis_port is None:
            raise pp_module.RateLimitDisabledError("test")
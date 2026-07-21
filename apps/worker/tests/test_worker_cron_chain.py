"""End-to-end тест worker-конвейера.

По docs/06: полный pipeline одного дня жизни клуба —
1) `close_catch_window` списывает штрафы за пропуски
2) `apply_catch_bonus` начисляет бонусы охотникам за каждый штраф с catcher'ом
3) `integrity_check_bonus_transactions` подтверждает отсутствие orphan-транзакций
4) `expire_bonus_points` не трогает свежие бонусы
"""
from __future__ import annotations


import pytest


@pytest.mark.asyncio
async def test_daily_cron_chain_full_day(worker_db):
    """Полный сценарий: пропуски → штрафы → бонусы → integrity → свежие бонусы сохранены."""
    from sqlalchemy import select

    from app.core.constants import PenaltyConfig, PenaltyReason
    from app.models.penalty import Penalty
    from app.models.transaction import Transaction
    from app.models.user import User
    from worker.tasks.apply_catch_bonus import _process as apply_bonus
    from worker.tasks.close_catch_window import _process as run_for_active_habits
    from worker.tasks.expire_bonus_points import _process as expire_bonus
    from worker.tasks.integrity_check_bonus_transactions import (
        _process as integrity_check,
    )


    # Участники клуба:
    # - violator: пропустил чек-ин
    # - catcher: поймал violator'а (заранее зарегистрирован через другой путь,
    #   в нашем конвейере мы создаём penalty напрямую с catcher'ом)
    async with worker_db.session_factory() as session:
        catcher_user = await worker_db.add_user(
            session, id=600, bonus_points=0
        )
        violator_user = await worker_db.add_user(
            session, id=601, bonus_points=0
        )
        habit = await worker_db.add_habit(
            session, id="00000000-0000-0000-0000-000000000fff"
        )
        catcher_m = await worker_db.add_membership(
            session,
            user_id=catcher_user.id,
            habit_id=habit.id,
            deposit_balance=0,  # catcher сам без депозита, не штрафуется
        )
        violator_m = await worker_db.add_membership(
            session, user_id=violator_user.id, habit_id=habit.id, deposit_balance=1000
        )
        await session.commit()
        catcher_m_id = catcher_m.id
        violator_m_id = violator_m.id

    # 1) close_catch_window: violator пропустил → штраф с reason=window_closed_no_catch
    #    catcher сам не штрафуется, т.к. в его habit нет пропуска.
    window_result = await run_for_active_habits()
    assert window_result["summary"] == [
        {"habit_id": habit.id, "penalized": 1}
    ]

    # Получаем penalty, который создал close_catch_window — и дораспределяем ему
    # catcher'а (имитация сценария, когда catch произошёл в течение catch-окна).
    async with worker_db.session_factory() as session:
        p_window = (
            await session.execute(
                select(Penalty).where(
                    Penalty.membership_id == violator_m_id,
                    Penalty.reason == PenaltyReason.WINDOW_CLOSED_NO_CATCH.value,
                )
            )
        ).scalar_one()
        assert p_window.catcher_membership_id is None
        assert p_window.amount == 100
        # Симулируем catch: ставим catcher и bonus_applied=False для перерасчёта.
        p_window.catcher_membership_id = catcher_m_id
        await session.commit()
        penalty_id = p_window.id

    # 2) apply_catch_bonus — начисляем +1 охотнику и пишем bonus_catch транзакцию
    bonus_result = await apply_bonus(
        {"catcher_membership_id": catcher_m_id, "penalty_id": penalty_id}
    , session_factory=worker_db.session_factory)
    assert bonus_result["ok"] is True
    assert bonus_result["applied"] == PenaltyConfig.CATCHER_BONUS_POINTS

    async with worker_db.session_factory() as session:
        u = (
            await session.execute(select(User).where(User.id == 600))
        ).scalar_one()
        assert u.bonus_points == PenaltyConfig.CATCHER_BONUS_POINTS

    # 3) integrity_check — penalty.bonus_applied=true имеет matching bonus_catch транзакцию.
    integrity = await integrity_check()
    assert integrity == {"orphans": 0}

    # 4) expire_bonus_points — свежие бонусы НЕ сгорают
    expire_result = await expire_bonus(session_factory=worker_db.session_factory)
    assert expire_result == {"expired": 0}

    async with worker_db.session_factory() as session:
        u = (
            await session.execute(select(User).where(User.id == 600))
        ).scalar_one()
        assert u.bonus_points == PenaltyConfig.CATCHER_BONUS_POINTS

    # Две транзакции: одна 'penalty' (от close_catch_window) и одна 'bonus_catch'.
    async with worker_db.session_factory() as session:
        txs = (await session.execute(select(Transaction))).scalars().all()
        assert len(txs) == 2
        tx_types = sorted(tx.type for tx in txs)
        assert tx_types == ["bonus_catch", "penalty"]

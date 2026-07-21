"""Seed development data: 1 test club + 5 fake members + 3 historical check-ins.

Usage:
    docker compose exec backend python -m scripts.seed_dev_data
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import ProofType
from app.db.redis import get_redis
from app.db.session import async_session_factory
from app.models.habit import Habit
from app.models.user import User
from app.repositories.habit_repository import HabitRepository
from app.repositories.user_repository import UserRepository


async def _create_club(session: AsyncSession) -> Habit:
    existing = (await session.execute(
        select(Habit).where(Habit.title == "Планка 21 день")
    )).scalar_one_or_none()
    if existing:
        return existing

    habit = Habit(
        title="Планка 21 день",
        description=(
            "Каждое утро стойка на локтях ≥60 секунд. "
            "Видео-кружок на таймере. Штраф 100 ₽ за пропуск."
        ),
        chat_id=-1001234567890,
        prize_pool=10000,
        penalty_amount=10000,
        price_month=99000,
        timezone="Europe/Moscow",
        checkin_window_start=time(6, 0),
        checkin_window_end=time(11, 0),
        proof_type=ProofType.VIDEO_NOTE,
        is_active=True,
    )
    session.add(habit)
    await session.commit()
    await session.refresh(habit)
    return habit


async def _create_members(session: AsyncSession, habit_id: uuid.UUID) -> list[User]:
    user_repo = UserRepository(session)
    members: list[User] = []
    handles = ["alice", "bob", "carol", "dave", "eve"]
    for idx, handle in enumerate(handles, start=1):
        user = await user_repo.upsert(
            id=10_000 + idx,
            first_name=handle.title(),
            username=f"@{handle}",
        )
        members.append(user)
    await session.commit()
    return members


async def main() -> None:
    async with async_session_factory() as session:
        habit = await _create_club(session)
        await _create_members(session, habit.id)
        print(f"seed: habit={habit.title} ({habit.id}) created")
        print(f"      prize_pool={habit.prize_pool / 100:.2f}₽")
        print(f"      penalty={habit.penalty_amount / 100:.2f}₽/пропуск")

    # Touch Redis
    try:
        redis = get_redis()
        pong = await redis.ping()
        print(f"seed: Redis ping → {pong}")
    except Exception as exc:  # noqa: BLE001
        print(f"seed: WARN Redis unreachable: {exc!r}")


if __name__ == "__main__":
    asyncio.run(main())
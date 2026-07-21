from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user_db
from app.core.security import TelegramUser
from app.db.session import get_session
from app.models.checkin import Checkin
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.user import User
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository


router = APIRouter()


class LeaderboardEntry(BaseModel):
    rank: int
    membership_id: str
    first_name: str
    metric_value: int


class LeaderboardResponse(BaseModel):
    items: list[LeaderboardEntry]


async def _build_rows(
    session: AsyncSession,
    metrics: dict[str, int],
) -> list[LeaderboardEntry]:
    membership_ids = list(metrics.keys())
    if not membership_ids:
        return []
    rows = (
        await session.execute(
            select(Membership.id, User.id, User.first_name)
            .join(User, User.id == Membership.user_id)
            .where(Membership.id.in_(membership_ids))
        )
    ).all()

    by_id_name = {str(m_id): (first_name) for m_id, _, first_name in rows}

    sorted_metrics = sorted(metrics.items(), key=lambda kv: kv[1], reverse=True)
    out: list[LeaderboardEntry] = []
    for rank, (m_id, value) in enumerate(sorted_metrics, start=1):
        out.append(
            LeaderboardEntry(
                rank=rank,
                membership_id=str(m_id),
                first_name=by_id_name.get(str(m_id), "—"),
                metric_value=value,
            )
        )
    return out


async def _streak_leaderboard(
    session: AsyncSession,
    membership_repo: MembershipRepository,
    habit_id: str,
) -> list[LeaderboardEntry]:
    members = await membership_repo.list_for_habit(habit_id)
    if not members:
        return []
    member_ids = [str(m.id) for m in members]

    habit_repo = HabitRepository(session)
    habit = await habit_repo.get(habit_id)
    if habit is None:
        raise HTTPException(404, "habit_not_found")
    today = habit.club_date(datetime.now(tz=timezone.utc))

    rows = (
        await session.execute(
            select(Checkin.membership_id, Checkin.date)
            .where(
                Checkin.membership_id.in_(member_ids),
                Checkin.date <= today,
                Checkin.status == "done",
            )
            .order_by(Checkin.date.desc())
        )
    ).all()

    days_by_member: dict[str, set[date]] = defaultdict(set)
    for m_id, d in rows:
        days_by_member[str(m_id)].add(d)

    streaks: dict[str, int] = {}
    for m_id, days in days_by_member.items():
        streak = 0
        cur = today
        from datetime import timedelta

        while cur in days:
            streak += 1
            cur = cur - timedelta(days=1)
        streaks[m_id] = streak

    return await _build_rows(session, streaks)


async def _catch_leaderboard(
    session: AsyncSession,
    membership_repo: MembershipRepository,
    habit_id: str,
) -> list[LeaderboardEntry]:
    members = await membership_repo.list_for_habit(habit_id)
    member_ids = [str(m.id) for m in members]

    rows = (
        await session.execute(
            select(Penalty.catcher_membership_id, func.count(Penalty.id))
            .where(
                Penalty.catcher_membership_id.in_(member_ids),
            )
            .group_by(Penalty.catcher_membership_id)
        )
    ).all()

    metrics = {str(m_id): int(count) for m_id, count in rows}
    return await _build_rows(session, metrics)


async def _shame_leaderboard(
    session: AsyncSession,
    membership_repo: MembershipRepository,
    habit_id: str,
) -> list[LeaderboardEntry]:
    members = await membership_repo.list_for_habit(habit_id)
    member_ids = [str(m.id) for m in members]

    rows = (
        await session.execute(
            select(Penalty.membership_id, func.count(Penalty.id))
            .where(Penalty.membership_id.in_(member_ids))
            .group_by(Penalty.membership_id)
        )
    ).all()

    metrics = {str(m_id): int(count) for m_id, count in rows}
    return await _build_rows(session, metrics)


@router.get("/habits/{habit_id}/leaderboard/streak", response_model=LeaderboardResponse)
async def streak(
    habit_id: str,
    _: TelegramUser = Depends(current_user_db),
    session: AsyncSession = Depends(get_session),
) -> LeaderboardResponse:
    rows = await _streak_leaderboard(
        session, MembershipRepository(session), habit_id
    )
    return LeaderboardResponse(items=rows)


@router.get("/habits/{habit_id}/leaderboard/catches", response_model=LeaderboardResponse)
async def catches(
    habit_id: str,
    _: TelegramUser = Depends(current_user_db),
    session: AsyncSession = Depends(get_session),
) -> LeaderboardResponse:
    rows = await _catch_leaderboard(
        session, MembershipRepository(session), habit_id
    )
    return LeaderboardResponse(items=rows)


@router.get("/habits/{habit_id}/leaderboard/shame", response_model=LeaderboardResponse)
async def shame(
    habit_id: str,
    _: TelegramUser = Depends(current_user_db),
    session: AsyncSession = Depends(get_session),
) -> LeaderboardResponse:
    rows = await _shame_leaderboard(
        session, MembershipRepository(session), habit_id
    )
    return LeaderboardResponse(items=rows)
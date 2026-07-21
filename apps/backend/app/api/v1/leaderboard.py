from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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


class OverviewClub(BaseModel):
    habit_id: str
    title: str
    members_count: int
    top: list[LeaderboardEntry]


class LeaderboardOverviewResponse(BaseModel):
    tab: str
    metric_label: str
    clubs: list[OverviewClub]


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


async def _global_streak(session: AsyncSession, user_id: int) -> list[LeaderboardEntry]:
    """Глобальные серии: сумма streak_days по всем активным клубам пользователя."""
    rows = (
        await session.execute(
            select(Checkin.date, Checkin.membership_id, Membership.user_id)
            .join(Membership, Membership.id == Checkin.membership_id)
            .where(Membership.status == "active", Checkin.status == "done")
        )
    ).all()

    # group by (user_id, membership_id) → set of dates
    dates_by_member: dict[tuple[int, str], set[date]] = defaultdict(set)
    member_to_user: dict[str, int] = {}
    for d, m_id, u_id in rows:
        dates_by_member[(u_id, str(m_id))].add(d)
        member_to_user[str(m_id)] = u_id

    metrics_per_user: dict[int, int] = defaultdict(int)
    for (u_id, _m_id), days in dates_by_member.items():
        streak = 0
        cur = max(days)
        from datetime import timedelta

        while cur in days:
            streak += 1
            cur = cur - timedelta(days=1)
        metrics_per_user[u_id] += streak

    if not metrics_per_user:
        return []
    user_ids = list(metrics_per_user.keys())
    name_rows = (
        await session.execute(select(User.id, User.first_name).where(User.id.in_(user_ids)))
    ).all()
    names = {u_id: name for u_id, name in name_rows}

    sorted_rows = sorted(metrics_per_user.items(), key=lambda kv: kv[1], reverse=True)
    return [
        LeaderboardEntry(
            rank=rank,
            membership_id=f"u:{u_id}",
            first_name=names.get(u_id, "—"),
            metric_value=value,
        )
        for rank, (u_id, value) in enumerate(sorted_rows, start=1)
        if value > 0
    ]


async def _global_counts(
    session: AsyncSession,
    *,
    column,
) -> list[LeaderboardEntry]:
    """Глобальные ловцы / позор: COUNT(penalty) GROUP BY user_id.

    column — Penalty.catcher_membership_id (ловцы) или Penalty.membership_id (позор).
    Считаем user_id нарушителя/ловца через join на Membership.
    """
    catcher_membership = aliased(Membership)
    rows = (
        await session.execute(
            select(catcher_membership.user_id, func.count(Penalty.id))
            .join(catcher_membership, catcher_membership.id == column)
            .where(catcher_membership.status == "active")
            .group_by(catcher_membership.user_id)
        )
    ).all()

    metrics: dict[int, int] = defaultdict(int)
    for u_id, count in rows:
        metrics[u_id] += int(count)

    if not metrics:
        return []
    user_ids = list(metrics.keys())
    name_rows = (
        await session.execute(select(User.id, User.first_name).where(User.id.in_(user_ids)))
    ).all()
    names = {u_id: name for u_id, name in name_rows}

    sorted_rows = sorted(metrics.items(), key=lambda kv: kv[1], reverse=True)
    return [
        LeaderboardEntry(
            rank=rank,
            membership_id=f"u:{u_id}",
            first_name=names.get(u_id, "—"),
            metric_value=value,
        )
        for rank, (u_id, value) in enumerate(sorted_rows, start=1)
        if value > 0
    ]


@router.get("/leaderboard/streak", response_model=LeaderboardResponse)
async def global_streak(
    user: TelegramUser = Depends(current_user_db),
    session: AsyncSession = Depends(get_session),
) -> LeaderboardResponse:
    rows = await _global_streak(session, user.id)
    return LeaderboardResponse(items=rows)


@router.get("/leaderboard/catches", response_model=LeaderboardResponse)
async def global_catches(
    _: TelegramUser = Depends(current_user_db),
    session: AsyncSession = Depends(get_session),
) -> LeaderboardResponse:
    rows = await _global_counts(session, column=Penalty.catcher_membership_id)
    return LeaderboardResponse(items=rows)


@router.get("/leaderboard/shame", response_model=LeaderboardResponse)
async def global_shame(
    _: TelegramUser = Depends(current_user_db),
    session: AsyncSession = Depends(get_session),
) -> LeaderboardResponse:
    rows = await _global_counts(session, column=Penalty.membership_id)
    return LeaderboardResponse(items=rows)


async def _overview_metric(
    session: AsyncSession,
    *,
    habit_id: str,
    tab: str,
) -> list[LeaderboardEntry]:
    """Возвращает топ-3 (или меньше) участников клуба по выбранной метрике.

    Использует уже готовые хелперы _streak_leaderboard / _catch_leaderboard /
    _shame_leaderboard и обрезает результат до 3.
    """
    membership_repo = MembershipRepository(session)
    if tab == "streak":
        rows = await _streak_leaderboard(session, membership_repo, habit_id)
    elif tab == "catches":
        rows = await _catch_leaderboard(session, membership_repo, habit_id)
    elif tab == "shame":
        rows = await _shame_leaderboard(session, membership_repo, habit_id)
    else:
        raise HTTPException(400, "tab must be streak|catches|shame")
    return rows[:3]


@router.get("/leaderboard/{tab}/overview", response_model=LeaderboardOverviewResponse)
async def leaderboard_overview(
    tab: str,
    user: TelegramUser = Depends(current_user_db),
    session: AsyncSession = Depends(get_session),
) -> LeaderboardOverviewResponse:
    """Сводка лидерборда по всем клубам юзера.

    Возвращает список клубов с топ-3 участников в каждом. Юзер может
    открыть любой клуб и посмотреть детали.
    """
    if tab not in ("streak", "catches", "shame"):
        raise HTTPException(400, "tab must be streak|catches|shame")

    metric_label = {"streak": "дн.", "catches": "поимок", "shame": "штрафов"}[tab]

    habit_repo = HabitRepository(session)
    user_habits = await habit_repo.list_for_user(user.id)

    clubs: list[OverviewClub] = []
    for habit in user_habits:
        top = await _overview_metric(session, habit_id=str(habit.id), tab=tab)
        members = await MembershipRepository(session).list_for_habit(str(habit.id))
        clubs.append(
            OverviewClub(
                habit_id=str(habit.id),
                title=habit.title,
                members_count=len(members),
                top=top,
            )
        )

    return LeaderboardOverviewResponse(tab=tab, metric_label=metric_label, clubs=clubs)
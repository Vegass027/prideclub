from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.v1.users import TelegramUserDbDep
from app.core.deps import SessionDep
from app.models.checkin import Checkin
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.user import User
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository

router = APIRouter()

# Жёсткий потолок размера лидерборда (Pravki.md §8.1, обсуждение от
# 2026-07-24). В клубе с 5 000 участников фронт не должен тащить 5 000
# аватарок — это 5 000 параллельных GET /photo, ~75s network I/O в браузере
# и мгновенный 429 от rate-limit middleware (60 req/min/user). Лимит 100
# даёт:
#   - backend CPU: 1 процесс справляется с 10 000 одновременных онлайн
#     (вместо ~500 без лимита)
#   - render time в браузере: ~1.5s вместо ~75s
#   - memory в браузере: ~3 MB вместо ~150 MB
# Если клуб < 100 — обрезка не применяется, отдаём весь список.
LEADERBOARD_LIMIT = 100


class LeaderboardEntry(BaseModel):
    rank: int
    membership_id: str
    first_name: str
    metric_value: int
    # Относительный путь к нашему photo-endpoint (см. Pravki.md §7.1).
    # Клиент сам построит абсолютный URL (под текущим origin).
    # None = нет аватарки или worker ещё не подтянул → fallback на инициалы.
    photo_url: str | None = None


class LeaderboardResponse(BaseModel):
    items: list[LeaderboardEntry]
    # Общее число участников с ненулевой метрикой (до обрезки).
    # None если обрезки не было. Используется UI: "Топ-100 из {total}".
    total: int | None = None


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
    # JOIN с users — достаём user_id (для photo_url), first_name, photo_file_id.
    rows = (
        await session.execute(
            select(
                Membership.id,
                User.id,
                User.first_name,
                User.photo_file_id,
            )
            .join(User, User.id == Membership.user_id)
            .where(Membership.id.in_(membership_ids))
        )
    ).all()

    by_membership: dict[str, dict] = {
        str(m_id): {
            "first_name": first_name,
            "user_id": user_id,
            "has_photo": bool(photo_file_id),
        }
        for m_id, user_id, first_name, photo_file_id in rows
    }

    sorted_metrics = sorted(metrics.items(), key=lambda kv: kv[1], reverse=True)
    out: list[LeaderboardEntry] = []
    for rank, (m_id, value) in enumerate(sorted_metrics, start=1):
        meta = by_membership.get(str(m_id))
        first_name = meta["first_name"] if meta else "—"
        photo_url: str | None = None
        if meta and meta["has_photo"]:
            photo_url = f"/api/v1/users/{meta['user_id']}/photo"
        out.append(
            LeaderboardEntry(
                rank=rank,
                membership_id=str(m_id),
                first_name=first_name,
                metric_value=value,
                photo_url=photo_url,
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
    today = habit.club_date(datetime.now(tz=UTC))

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

    return await _build_rows(session, _truncate_metrics(streaks))


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
    return await _build_rows(session, _truncate_metrics(metrics))


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
    return await _build_rows(session, _truncate_metrics(metrics))


def _truncate_metrics(metrics: dict[str, int]) -> dict[str, int]:
    """Оставляет топ-N (LEADERBOARD_LIMIT) по убыванию value.

    Остальные не попадают в _build_rows → не в SQL JOIN → не возвращаются
    клиенту. Полное число юзеров с ненулевой метрикой возвращается через
    `total` в response (вызывающий код отдельно сохраняет len(metrics)).
    """
    if len(metrics) <= LEADERBOARD_LIMIT:
        return metrics
    # heapq.nlargest — O(N log K) вместо полной сортировки O(N log N).
    import heapq

    top = heapq.nlargest(LEADERBOARD_LIMIT, metrics.items(), key=lambda kv: kv[1])
    return dict(top)


@router.get("/habits/{habit_id}/leaderboard/streak", response_model=LeaderboardResponse)
async def streak(
    habit_id: str,
    _: TelegramUserDbDep,
    session: SessionDep,
) -> LeaderboardResponse:
    rows = await _streak_leaderboard(
        session, MembershipRepository(session), habit_id
    )
    # Для локального лидерборда: total = len(rows) если обрезали
    # (потому что после _truncate_metrics+_build_rows мы уже не знаем
    # исходное число). rows < LEADERBOARD_LIMIT → total=None.
    total = len(rows) if len(rows) >= LEADERBOARD_LIMIT else None
    return LeaderboardResponse(items=rows, total=total)


@router.get("/habits/{habit_id}/leaderboard/catches", response_model=LeaderboardResponse)
async def catches(
    habit_id: str,
    _: TelegramUserDbDep,
    session: SessionDep,
) -> LeaderboardResponse:
    rows = await _catch_leaderboard(
        session, MembershipRepository(session), habit_id
    )
    total = len(rows) if len(rows) >= LEADERBOARD_LIMIT else None
    return LeaderboardResponse(items=rows, total=total)


@router.get("/habits/{habit_id}/leaderboard/shame", response_model=LeaderboardResponse)
async def shame(
    habit_id: str,
    _: TelegramUserDbDep,
    session: SessionDep,
) -> LeaderboardResponse:
    rows = await _shame_leaderboard(
        session, MembershipRepository(session), habit_id
    )
    total = len(rows) if len(rows) >= LEADERBOARD_LIMIT else None
    return LeaderboardResponse(items=rows, total=total)


async def _global_streak(
    session: AsyncSession, user_id: int
) -> tuple[list[LeaderboardEntry], int]:
    """Глобальные серии: сумма streak_days по всем активным клубам пользователя.

    Returns:
        (rows, total) — rows обрезан до LEADERBOARD_LIMIT, total — общее
        число юзеров с ненулевой метрикой (до обрезки).
    """
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

    return _build_global_rows(session, metrics_per_user)


async def _global_counts(
    session: AsyncSession,
    *,
    column,
) -> tuple[list[LeaderboardEntry], int]:
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

    return _build_global_rows(session, metrics)


async def _build_global_rows(
    session: AsyncSession,
    metrics: dict[int, int],
) -> tuple[list[LeaderboardEntry], int]:
    """Сортирует metrics, обрезает до LEADERBOARD_LIMIT, возвращает (rows, total).

    total = len(metrics) — общее число юзеров с ненулевой метрикой.
    None total = обрезки не было (клуб < LEADERBOARD_LIMIT).
    """
    total = len(metrics)
    if total == 0:
        return [], None

    # Сортируем и обрезаем по value desc
    if total > LEADERBOARD_LIMIT:
        import heapq

        top = heapq.nlargest(
            LEADERBOARD_LIMIT, metrics.items(), key=lambda kv: kv[1]
        )
    else:
        top = sorted(metrics.items(), key=lambda kv: kv[1], reverse=True)

    user_ids = [u_id for u_id, _ in top]
    name_rows = (
        await session.execute(
            select(User.id, User.first_name, User.photo_file_id).where(
                User.id.in_(user_ids)
            )
        )
    ).all()
    by_user: dict[int, dict] = {
        u_id: {"first_name": fn, "has_photo": bool(pf)}
        for u_id, fn, pf in name_rows
    }

    out: list[LeaderboardEntry] = []
    for rank, (u_id, value) in enumerate(top, start=1):
        if value <= 0:
            continue
        meta = by_user.get(u_id)
        first_name = meta["first_name"] if meta else "—"
        photo_url: str | None = (
            f"/api/v1/users/{u_id}/photo" if meta and meta["has_photo"] else None
        )
        out.append(
            LeaderboardEntry(
                rank=rank,
                membership_id=f"u:{u_id}",
                first_name=first_name,
                metric_value=value,
                photo_url=photo_url,
            )
        )

    # total = None если обрезки не было (= влезает в LEADERBOARD_LIMIT),
    # иначе реальное число. UI решает: "Показаны топ-100 из N" или ничего.
    return out, (total if total > LEADERBOARD_LIMIT else None)


@router.get("/leaderboard/streak", response_model=LeaderboardResponse)
async def global_streak(
    user: TelegramUserDbDep,
    session: SessionDep,
) -> LeaderboardResponse:
    rows, total = await _global_streak(session, user.id)
    return LeaderboardResponse(items=rows, total=total)


@router.get("/leaderboard/catches", response_model=LeaderboardResponse)
async def global_catches(
    _: TelegramUserDbDep,
    session: SessionDep,
) -> LeaderboardResponse:
    rows, total = await _global_counts(session, column=Penalty.catcher_membership_id)
    return LeaderboardResponse(items=rows, total=total)


@router.get("/leaderboard/shame", response_model=LeaderboardResponse)
async def global_shame(
    _: TelegramUserDbDep,
    session: SessionDep,
) -> LeaderboardResponse:
    rows, total = await _global_counts(session, column=Penalty.membership_id)
    return LeaderboardResponse(items=rows, total=total)


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
    user: TelegramUserDbDep,
    session: SessionDep,
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
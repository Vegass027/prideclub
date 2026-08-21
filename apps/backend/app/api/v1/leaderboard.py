from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.v1.users import TelegramUserDbDep
from app.core.deps import SessionDep
from app.core.exceptions import (
    HabitArchivedError,
    HabitNotFoundError,
)
from app.models.checkin import Checkin
from app.models.membership import Membership
from app.models.penalty import Penalty
from app.models.user import User
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_stats_repository import (
    HabitLeaderboardRow,
    UserStatsRepository,
)
from app.schemas import (
    CharacterResponse,
    CharacterStatOut,
    CharacterStatusInfo,
    StatLeaderboardEntry,
    StatLeaderboardResponse,
)

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


class LeaderboardBreakdown(BaseModel):
    """Доп. метрики для строки лидерборда.

    primary = `metric_value` (sortable, выбирается по табу).
    breakdown — остальные 3 числа для UI ("🔥 X / 💸 Y ₽ / 🎯 Z").
    Не участвуют в сортировке и не показываются как primary.
    """

    checkin_count: int
    streak_days: int
    penalties_count: int
    catches_count: int


class LeaderboardEntry(BaseModel):
    rank: int
    membership_id: str
    first_name: str
    metric_value: int
    breakdown: LeaderboardBreakdown
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


class LeaderboardClub(BaseModel):
    """Клуб в списке для глобального рейтинга (Pravki §7 v3.2).

    Минимум полей — UI рендерит "〖Название〗 — N УЧАСТНИКОВ".
    """

    habit_id: str
    title: str
    members_count: int


class LeaderboardClubsResponse(BaseModel):
    """Список клубов юзера для выбранной категории."""

    tab: str
    metric_label: str
    clubs: list[LeaderboardClub]


async def _membership_breakdown(
    session: AsyncSession,
    membership_ids: list[str],
) -> dict[str, LeaderboardBreakdown]:
    """Одним SQL-пакетом возвращает breakdown для каждого membership.

    Считает 4 числа параллельно:
        checkin_count = COUNT(checkin WHERE done)
        streak_days   — НЕ вычисляем здесь (per-row, нужен Python-цикл).
                        Возвращаем 0 — клиент видит streak только для себя
                        (TodayPage), в лидерборде streak не показываем.
                        Это сознательное упрощение: streak = O(N) Python
                        для каждой строки лидерборда не оправдан.
        penalties_count = COUNT(penalty WHERE violator)
        catches_count  = COUNT(penalty WHERE catcher)
    """
    from app.repositories.checkin_repository import CheckinRepository
    from app.repositories.penalty_repository import PenaltyRepository

    if not membership_ids:
        return {}

    checkin_repo = CheckinRepository(session)
    penalty_repo = PenaltyRepository(session)

    checkin_by = await checkin_repo.count_done_for_memberships(membership_ids)
    viol_by = await penalty_repo.totals_for_memberships(
        membership_ids, as_violator=True
    )
    catch_by = await penalty_repo.totals_for_memberships(
        membership_ids, as_violator=False
    )

    return {
        m_id: LeaderboardBreakdown(
            checkin_count=checkin_by.get(m_id, 0),
            streak_days=0,
            penalties_count=viol_by.get(m_id, (0, 0))[0],
            catches_count=catch_by.get(m_id, (0, 0))[0],
        )
        for m_id in membership_ids
    }


async def _user_breakdown(
    session: AsyncSession,
    user_ids: list[int],
) -> dict[int, LeaderboardBreakdown]:
    """Per-user breakdown для global leaderboard — суммирует по active memberships."""
    from sqlalchemy import select

    from app.core.constants import MembershipStatus
    from app.repositories.checkin_repository import CheckinRepository
    from app.repositories.penalty_repository import PenaltyRepository

    if not user_ids:
        return {}

    mem_rows = (
        await session.execute(
            select(Membership.user_id, Membership.id).where(
                Membership.user_id.in_(user_ids),
                Membership.status == MembershipStatus.ACTIVE,
            )
        )
    ).all()
    user_to_memberships: dict[int, list[str]] = {}
    for uid, mid in mem_rows:
        user_to_memberships.setdefault(int(uid), []).append(str(mid))

    all_membership_ids: list[str] = [
        m for mids in user_to_memberships.values() for m in mids
    ]
    if not all_membership_ids:
        return {
            uid: LeaderboardBreakdown(
                checkin_count=0, streak_days=0, penalties_count=0, catches_count=0
            )
            for uid in user_ids
        }

    checkin_repo = CheckinRepository(session)
    penalty_repo = PenaltyRepository(session)
    checkin_by_mem = await checkin_repo.count_done_for_memberships(all_membership_ids)
    viol_by_mem = await penalty_repo.totals_for_memberships(
        all_membership_ids, as_violator=True
    )
    catch_by_mem = await penalty_repo.totals_for_memberships(
        all_membership_ids, as_violator=False
    )

    out: dict[int, LeaderboardBreakdown] = {}
    for uid in user_ids:
        mids = user_to_memberships.get(uid, [])
        out[uid] = LeaderboardBreakdown(
            checkin_count=sum(checkin_by_mem.get(m, 0) for m in mids),
            streak_days=0,
            penalties_count=sum(viol_by_mem.get(m, (0, 0))[0] for m in mids),
            catches_count=sum(catch_by_mem.get(m, (0, 0))[0] for m in mids),
        )
    return out


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

    breakdown_by_membership = await _membership_breakdown(session, membership_ids)

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
                breakdown=breakdown_by_membership.get(
                    str(m_id),
                    LeaderboardBreakdown(
                        checkin_count=0, streak_days=0, penalties_count=0, catches_count=0
                    ),
                ),
                photo_url=photo_url,
            )
        )
    return out


async def _streak_leaderboard(
    session: AsyncSession,
    membership_repo: MembershipRepository,
    habit_id: str,
) -> list[LeaderboardEntry]:
    """Чекины: COUNT(done) по каждому membership в клубе.

    Семантика (Pravki.md 2026-07-24): "сколько раз отчекинился, столько
    и на счетчике". Раньше здесь был consecutive streak от today назад
    — сбрасывался в 0 если сегодня не отчекинился. Юзер ожидал total
    count всех done-чекинов за всё время. Логика заменена: метрика =
    суммарное число done-чекинов membership ≤ today (без требования
    непрерывности).
    """
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
            select(Checkin.membership_id, func.count(Checkin.id))
            .where(
                Checkin.membership_id.in_(member_ids),
                Checkin.date <= today,
                Checkin.status == "done",
            )
            .group_by(Checkin.membership_id)
        )
    ).all()

    counts: dict[str, int] = {str(m_id): int(c) for m_id, c in rows}
    return await _build_rows(session, _truncate_metrics(counts))


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
    """Глобальные чекины: SUM(COUNT(done)) по всем активным клубам юзера.

    Семантика (Pravki.md 2026-07-24): "сколько раз отчекинился за всё
    время, столько и на счетчике". Суммируем done-чекины во всех активных
    memberships юзера. Сначала group by (user_id, membership_id) →
    count, потом group by user_id → sum. Сложная агрегация в SQL
    делается одним запросом с подзапросом.
    """
    per_member_sq = (
        select(
            Membership.user_id.label("user_id"),
            Checkin.membership_id.label("membership_id"),
            func.count(Checkin.id).label("c"),
        )
        .join(Checkin, Checkin.membership_id == Membership.id)
        .where(Membership.status == "active", Checkin.status == "done")
        .group_by(Membership.user_id, Checkin.membership_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(per_member_sq.c.user_id, func.sum(per_member_sq.c.c))
            .group_by(per_member_sq.c.user_id)
        )
    ).all()
    metrics: dict[int, int] = {int(uid): int(total) for uid, total in rows}
    return await _build_global_rows(session, metrics)


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

    return await _build_global_rows(session, metrics)


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

    breakdown_by_user = await _user_breakdown(session, user_ids)

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
                breakdown=breakdown_by_user.get(
                    u_id,
                    LeaderboardBreakdown(
                        checkin_count=0, streak_days=0, penalties_count=0, catches_count=0
                    ),
                ),
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


@router.get("/leaderboard/{tab}/clubs", response_model=LeaderboardClubsResponse)
async def leaderboard_clubs(
    tab: str,
    user: TelegramUserDbDep,
    session: SessionDep,
) -> LeaderboardClubsResponse:
    """Список клубов юзера для категории таба (Pravki §7 v3.2, ребрендинг).

    Возвращает только клубы, в которых user — active member. Без
    top-3 (UI делает отдельный запрос на /habits/{id}/leaderboard).
    Используется в /leaderboards — список аккордеонов с простым
    текстом "название клуба — N УЧАСТНИКОВ".

    tab in {"streak", "catches", "shame"} → metric_label локализован
    (Серии / Охотники / Лентяи) для UI.
    """
    if tab not in ("streak", "catches", "shame"):
        raise HTTPException(400, "tab must be streak|catches|shame")

    metric_label = {
        "streak": "Серии",
        "catches": "Охотники",
        "shame": "Лентяи",
    }[tab]

    habit_repo = HabitRepository(session)
    membership_repo = MembershipRepository(session)

    # Активные клубы юзера (без archived).
    user_habits = await habit_repo.list_for_user(user.id)
    clubs: list[LeaderboardClub] = []
    for habit in user_habits:
        members = await membership_repo.list_for_habit(str(habit.id))
        clubs.append(
            LeaderboardClub(
                habit_id=str(habit.id),
                title=habit.title,
                members_count=len(members),
            )
        )

    return LeaderboardClubsResponse(tab=tab, metric_label=metric_label, clubs=clubs)


# ── Phase 3 v2 Task 3.6: per-habit stat-leaderboard ──────────────────

@router.get(
    "/habits/{habit_id}/leaderboard",
    response_model=StatLeaderboardResponse,
)
async def stat_leaderboard(
    habit_id: str,
    _: TelegramUserDbDep,
    session: SessionDep,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=LEADERBOARD_LIMIT,
            description=(
                f"Макс. {LEADERBOARD_LIMIT}. Запрос выше кэппится 422 (Pydantic)."
            ),
        ),
    ] = LEADERBOARD_LIMIT,
) -> StatLeaderboardResponse:
    """Глобальный лидерборд по характеристике клуба (Phase 3 v2).

    Один read-query в UserStatsRepository.list_for_habit_leaderboard:
    фильтр membership.status='active' и stat_definition_id в WHERE
    ДО ORDER BY и LIMIT (per Task 3.5 fix-3-pattern). Frozen stats
    остаются в выдаче (UI рисует ❄).

    Errors:
      - habit not found → HabitNotFoundError → 404
      - habit archived  → HabitArchivedError → 404
      - habit.stat_definition_id IS NULL → 200 + items=[]
        (клуб существует, feature не активирована админом).

    Total семантика (как у существующих 3 leaderboard handlers):
      None = обрезки не было (len < limit),
      иначе = len(items) = limit.
    """
    habit_repo = HabitRepository(session)
    user_stats_repo = UserStatsRepository(session)

    habit = await habit_repo.get(habit_id)
    if habit is None:
        raise HabitNotFoundError(habit_id=habit_id)
    if habit.archived_at is not None:
        raise HabitArchivedError(habit_id=habit_id)

    stat_def_id = habit.stat_definition_id
    if stat_def_id is None:
        # Клуб без выбранной характеристики → пустой лидерборд (200 OK).
        # Не ошибка: фича просто не активирована админом.
        return StatLeaderboardResponse(items=[], total=None)

    rows = await user_stats_repo.list_for_habit_leaderboard(
        habit_id=habit_id,
        stat_definition_id=stat_def_id,
        limit=limit,
    )
    items = [
        StatLeaderboardEntry(
            membership_id=r.membership_id,
            user_id=r.user_id,
            first_name=r.first_name,
            value=r.value,
            is_frozen=r.is_frozen,
        )
        for r in rows
    ]
    total = len(items) if len(items) >= LEADERBOARD_LIMIT else None
    return StatLeaderboardResponse(items=items, total=total)
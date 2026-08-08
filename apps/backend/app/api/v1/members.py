from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import TelegramUserDbDep
from app.core.constants import CheckinStatus
from app.core.deps import RedisDep, SessionDep
from app.core.exceptions import HabitArchivedError, PenaltyAlreadyProcessedError
from app.models.checkin import Checkin
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
from app.services.catch_rate_limiter import RedisCatchRateLimiter
from app.services.penalty_service import PenaltyService

router = APIRouter()


class MemberRowOut(BaseModel):
    """Строка члена клуба в списке участников.

    checkin_count — общее число done-чекинов за всё время
    (Pravki.md 2026-07-24: "сколько отчекинился, столько и на счетчике").
    Раньше был `streak_days=0` (заглушка).

    photo_url — relative путь /api/v1/users/{id}/photo (Pravki §7.1 v3.1).
    NULL = нет аватарки или worker не подтянул. Frontend оборачивает
    в new URL(photo_url, window.location.origin).
    """

    membership_id: str
    user_id: int
    first_name: str
    username: str | None
    status: str
    checkin_count: int
    can_catch: bool
    photo_url: str | None = None


class MembersResponse(BaseModel):
    items: list[MemberRowOut]


class CatchRequest(BaseModel):
    violator_membership_id: str


class CatchResponse(BaseModel):
    ok: bool
    code: str | None = None
    amount: int | None = None


@router.get("/habits/{habit_id}/members", response_model=MembersResponse)
async def list_members(
    habit_id: str,
    user: TelegramUserDbDep,
    session: SessionDep,
) -> MembersResponse:
    habit_repo = HabitRepository(session)
    membership_repo = MembershipRepository(session)
    checkin_repo = CheckinRepository(session)

    habit = await habit_repo.get(habit_id)
    if habit is None or habit.archived_at is not None:
        raise HabitArchivedError()

    memberships = await membership_repo.list_for_habit(habit_id)
    club_date = habit.club_date(datetime.now(tz=UTC))

    # Хак: достаём user.first_name из БД. В шаге 6 добавим UserRepository.
    user_id_to_name = await _user_names(session, [m.user_id for m in memberships])
    # photo_file_id для аватарок (Pravki §7.1 v3.1).
    user_id_to_photo = await _user_photo_file_ids(
        session, [m.user_id for m in memberships]
    )

    # Одним запросом достаём COUNT(done) GROUP BY membership_id для всех
    # членов клуба. Раньше возвращалось streak_days=0 (заглушка).
    member_ids = [str(m.id) for m in memberships]
    counts: dict[str, int] = {}
    if member_ids:
        rows = (
            await session.execute(
                select(Checkin.membership_id, func.count(Checkin.id))
                .where(
                    Checkin.membership_id.in_(member_ids),
                    Checkin.status == CheckinStatus.DONE,
                )
                .group_by(Checkin.membership_id)
            )
        ).all()
        counts = {str(m_id): int(c) for m_id, c in rows}

    now = datetime.now(tz=UTC)
    members: list[MemberRowOut] = []
    for m in memberships:
        existing = await checkin_repo.get_for_date(str(m.id), club_date)
        if existing is not None:
            status = existing.status.value
        else:
            status = "pending" if habit.is_within_checkin_window(now) else "missed"

        # photo_url: relative путь, frontend обернёт в absolute URL
        # (Pravki §7.1 v3.1, nginx try_files на /api/v1/users/N/photo).
        photo_url = (
            f"/api/v1/users/{m.user_id}/photo"
            if m.user_id in user_id_to_photo
            else None
        )

        members.append(
            MemberRowOut(
                membership_id=str(m.id),
                user_id=m.user_id,
                first_name=user_id_to_name.get(m.user_id, "—"),
                username=None,
                status=status,
                checkin_count=counts.get(str(m.id), 0),
                can_catch=user.id != m.user_id and status == "missed",
                photo_url=photo_url,
            )
        )

    return MembersResponse(items=members)


async def _user_names(session: AsyncSession, user_ids: list[int]) -> dict[int, str]:
    from sqlalchemy import select

    from app.models.user import User

    if not user_ids:
        return {}
    result = await session.execute(select(User.id, User.first_name).where(User.id.in_(user_ids)))
    return {row[0]: row[1] for row in result.all()}


async def _user_photo_file_ids(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, str]:
    """Возвращает {user_id: photo_file_id} для юзеров с фото.

    NULL photo_file_id → не возвращаем. Используется для построения
    photo_url на фронте (Pravki §7.1 v3.1).
    """
    from sqlalchemy import select

    from app.models.user import User

    if not user_ids:
        return {}
    result = await session.execute(
        select(User.id, User.photo_file_id).where(
            User.id.in_(user_ids), User.photo_file_id.is_not(None)
        )
    )
    return {row[0]: row[1] for row in result.all()}


@router.post("/habits/{habit_id}/catch", response_model=CatchResponse)
async def catch_violator(
    habit_id: str,
    payload: CatchRequest,
    user: TelegramUserDbDep,
    session: SessionDep,
    redis: RedisDep,
) -> CatchResponse:
    habit_repo = HabitRepository(session)
    membership_repo = MembershipRepository(session)
    checkin_repo = CheckinRepository(session)
    suspicious_repo = SuspiciousPairsRepository(session)
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        suspicious_repo=suspicious_repo,
        redis_port=RedisCatchRateLimiter(redis),
    )

    catcher_membership = await membership_repo.get_for_user_in_habit(user.id, habit_id)
    club_date = (await habit_repo.get(habit_id)).club_date(datetime.now(tz=UTC))  # type: ignore[union-attr]

    try:
        penalty = await service.apply_catch(
            catcher_user_id=user.id,
            violator_membership_id=payload.violator_membership_id,
            club_date=club_date,
            catcher_membership_id=str(catcher_membership.id) if catcher_membership else None,
        )
        await session.commit()
        return CatchResponse(ok=True, amount=penalty.amount)
    except PenaltyAlreadyProcessedError as exc:
        await session.rollback()
        return CatchResponse(ok=False, code=exc.code)
    except IntegrityError:
        # Pravki-deposit-sse.md §Z-2.8: гонка двух параллельных catch'ей на одну
        # и ту же (membership_id, date, reason). UNIQUE-индекс uq_penalty_per_day_reason
        # (миграция 002) срабатывает на INSERT второй транзакции. session.commit()
        # бросает IntegrityError — для юзера это эквивалентно "уже обработано".
        await session.rollback()
        return CatchResponse(ok=False, code="penalty_already_processed")
    except Exception as exc:
        await session.rollback()
        from app.core.exceptions import DomainError

        if isinstance(exc, DomainError):
            return CatchResponse(ok=False, code=exc.code)
        raise
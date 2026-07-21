from __future__ import annotations

from datetime import date as _date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user_internal
from app.core.exceptions import DomainError
from app.db.redis import get_redis
from app.db.session import get_session
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.services.catch_rate_limiter import RedisCatchRateLimiter
from app.services.penalty_service import PenaltyService


router = APIRouter()


class PenaltyProcessRequest(BaseModel):
    catcher_user_id: int
    catcher_membership_id: str | None
    violator_membership_id: str
    club_date: _date


class PenaltyResponse(BaseModel):
    ok: bool
    penalty_id: str | None = None
    code: str | None = None


def _build_service(session: AsyncSession, redis: Redis) -> PenaltyService:
    return PenaltyService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
        checkin_repo=CheckinRepository(session),
        redis_port=RedisCatchRateLimiter(redis),
    )


@router.post("/penalties/process", response_model=PenaltyResponse)
async def process_penalty_internal(
    payload: PenaltyProcessRequest,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    _: str = Depends(current_user_internal),
) -> PenaltyResponse:
    service = _build_service(session, redis)
    try:
        penalty = await service.apply_catch(
            catcher_user_id=payload.catcher_user_id,
            violator_membership_id=payload.violator_membership_id,
            club_date=payload.club_date,
            catcher_membership_id=payload.catcher_membership_id,
        )
        await session.commit()
        return PenaltyResponse(ok=True, penalty_id=str(penalty.id))
    except DomainError as exc:
        await session.rollback()
        return PenaltyResponse(ok=False, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        return PenaltyResponse(ok=False, code=f"internal:{type(exc).__name__}")
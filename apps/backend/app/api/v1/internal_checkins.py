from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import current_user_internal
from app.db.session import get_session
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.schemas import CheckinIngestPayload, InternalCheckinResult
from app.services.checkin_service import CheckinService
from app.services.proof_validator import ProofMessage, ProofValidationError
from app.services.today_cache import RedisTodayCache
from app.db.redis import get_redis
from app.core.constants import ProofType


router = APIRouter()


def _redis_enabled() -> bool:
    from app.core.config import get_settings

    try:
        return bool(get_settings().redis_url)
    except Exception:
        return False


async def _build_service(session: AsyncSession) -> CheckinService:
    cache = RedisTodayCache(get_redis()) if _redis_enabled() else None
    return CheckinService(
        session=session,
        habit_repo=HabitRepository(session),
        membership_repo=MembershipRepository(session),
        checkin_repo=CheckinRepository(session),
        cache=cache,
    )


@router.post("/checkins/process", response_model=InternalCheckinResult)
async def process_checkin_internal(
    payload: CheckinIngestPayload,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(current_user_internal),
) -> InternalCheckinResult:
    """Internal endpoint: Bot Gateway / Worker → Backend.

    Auth: X-Service-Token (уже проверен middleware).
    """
    habit_repo = HabitRepository(session)
    membership_repo = MembershipRepository(session)

    habit = await habit_repo.get_by_chat_id(payload.chat_id)
    if habit is None:
        return InternalCheckinResult(checkin_id=None, accepted=False, code="habit_not_found")

    # Membership можно не валидировать здесь — сервис сделает это и вернёт доменную ошибку.
    proof = ProofMessage(
        proof_type=ProofType(payload.proof_type),
        text=payload.text,
        video_note_duration=payload.duration_seconds,
        photo_sizes=1 if payload.proof_type == "photo" else 0,
        message_date=payload.message_sent_at,
    )

    service = await _build_service(session)
    try:
        checkin = await service.process_checkin(
            user_id=payload.user_id,
            habit_id=str(habit.id),
            proof=proof,
            proof_message_id=payload.message_id,
            now_utc=datetime.now(tz=timezone.utc),
        )
    except ProofValidationError as exc:
        return InternalCheckinResult(checkin_id=None, accepted=False, code=exc.code)
    except Exception as exc:  # маппинг доменных ошибок в стабильные коды
        from app.core.exceptions import (
            CheckinAlreadyExistsError,
            CheckinWindowClosedError,
            MembershipNotActiveError,
            MembershipNotFoundError,
        )

        if isinstance(exc, (CheckinWindowClosedError, MembershipNotActiveError, MembershipNotFoundError)):
            code = exc.code
        elif isinstance(exc, CheckinAlreadyExistsError):
            code = "checkin_already_exists"
        else:
            code = "internal_error"
        return InternalCheckinResult(checkin_id=None, accepted=False, code=code)

    return InternalCheckinResult(
        checkin_id=str(checkin.id), accepted=True, code="ok"
    )
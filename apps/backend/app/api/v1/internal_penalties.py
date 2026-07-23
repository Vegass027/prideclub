from __future__ import annotations

from datetime import date as _date
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import ServiceCallerDep
from app.core.deps import SessionDep
from app.core.logging import get_logger
from app.models.membership import Membership
from app.repositories.membership_repository import MembershipRepository
from app.services.celery_producer import send_task

router = APIRouter()


class PenaltyProcessRequest(BaseModel):
    catcher_user_id: int
    catcher_membership_id: str | None
    violator_membership_id: str
    club_date: _date


class PenaltyEnqueueResponse(BaseModel):
    ok: bool
    task_id: str | None = None
    code: str | None = None


async def _resolve_catcher_membership_id(
    session: AsyncSession, catcher_user_id: int
) -> str | None:
    """Если catcher_membership_id не передан, берём membership из того же habit,
    что у нарушителя.
    """
    stmt = select(Membership).where(Membership.user_id == catcher_user_id)
    rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return None
    active = [m for m in rows if m.status.value == "active"]
    return str(active[0].id) if active else None


@router.post("/penalties/catch", response_model=PenaltyEnqueueResponse)
async def enqueue_catch_penalty(
    payload: PenaltyProcessRequest,
    session: SessionDep,
    _: ServiceCallerDep,
) -> PenaltyEnqueueResponse:
    """Internal endpoint: бот/MiniApp → backend → Celery worker.

    НИКАКОЙ бизнес-логики здесь — только валидация membership + постановка задачи
    в очередь. Штраф списывается в worker-таске `process_penalty.run` в одной
    транзакции (см. docs/02 §5). Это:
      - разгружает API при пиках (массовое «спаливание» утром);
      - даёт ретраи на сетевые/HTTP ошибки;
      - изолирует DB-логику от webhook-таймаутов бота.

    Auth: X-Service-Token (уже проверен middleware).
    """
    log = get_logger("penalty_enqueue")

    # Базовая sanity-check: violator должен существовать.
    repo = MembershipRepository(session)
    violator = await repo.get(payload.violator_membership_id)
    if violator is None:
        return PenaltyEnqueueResponse(ok=False, code="violator_not_found")

    # Если catcher_membership_id не указан — пробуем вывести из membership
    # активного клуба (полезно для бота, который присылает только user_id).
    catcher_mid = payload.catcher_membership_id
    if catcher_mid is None:
        catcher_mid = await _resolve_catcher_membership_id(session, payload.catcher_user_id)

    task_id = send_task(
        "penalty",
        {
            "catcher_user_id": payload.catcher_user_id,
            "catcher_membership_id": catcher_mid,
            "violator_membership_id": payload.violator_membership_id,
            "club_date": payload.club_date.isoformat(),
            "enqueued_at": uuid4().hex,  # для трейсинга в логах
        },
    )

    log.info(
        "penalty_enqueued",
        extra={
            "task_id": task_id,
            "catcher_user_id": payload.catcher_user_id,
            "violator_membership_id": payload.violator_membership_id,
            "club_date": payload.club_date.isoformat(),
        },
    )
    return PenaltyEnqueueResponse(ok=True, task_id=task_id)
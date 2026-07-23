"""Admin API для управления suspicious_pairs.

Только два admin-action: clear или ban (см. docs/06-data-model §7.3).
Никаких manual-investigate — это требование антифрод-инварианта.

Доступ: /api/v1/admin/* — через service-token (current_user_internal).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.v1.users import ServiceCallerDep
from app.core.constants import SuspiciousPairStatus
from app.core.deps import SessionDep
from app.models.auxiliary import SuspiciousPair
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
from app.services.suspicious_pairs_service import SuspiciousPairsService

router = APIRouter()


class SuspiciousPairOut(BaseModel):
    membership_id_a: str
    membership_id_b: str
    reason: str
    status: str
    detected_at: str

    @classmethod
    def from_model(cls, m: SuspiciousPair) -> SuspiciousPairOut:
        return cls(
            membership_id_a=str(m.membership_id_a),
            membership_id_b=str(m.membership_id_b),
            reason=m.reason,
            status=m.status,
            detected_at=m.detected_at.isoformat() if m.detected_at else "",
        )


class SuspiciousPairsListResponse(BaseModel):
    items: list[SuspiciousPairOut]
    total: int


class FlagRequest(BaseModel):
    membership_id_a: str
    membership_id_b: str
    reason: str
    status: str = SuspiciousPairStatus.FLAGGED.value  # "flagged" | "banned"


class ClearRequest(BaseModel):
    membership_id_a: str
    membership_id_b: str


class ActionResponse(BaseModel):
    ok: bool
    status: str | None = None
    code: str | None = None


@router.get(
    "/admin/suspicious_pairs",
    response_model=SuspiciousPairsListResponse,
)
async def list_suspicious_pairs(
    session: SessionDep,
    _caller: ServiceCallerDep,
    status: str = Query(default=SuspiciousPairStatus.FLAGGED.value),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SuspiciousPairsListResponse:
    if status not in {
        SuspiciousPairStatus.FLAGGED.value,
        SuspiciousPairStatus.CLEARED.value,
        SuspiciousPairStatus.BANNED.value,
    }:
        raise HTTPException(400, "invalid_status")
    repo = SuspiciousPairsRepository(session)
    items = await repo.list_flagged(status=status, limit=limit, offset=offset)
    return SuspiciousPairsListResponse(
        items=[SuspiciousPairOut.from_model(p) for p in items],
        total=len(items),
    )


@router.post("/admin/suspicious_pairs/flag", response_model=ActionResponse)
async def flag_pair(
    payload: FlagRequest,
    session: SessionDep,
    _caller: ServiceCallerDep,
) -> ActionResponse:
    if payload.status not in {
        SuspiciousPairStatus.FLAGGED.value,
        SuspiciousPairStatus.BANNED.value,
    }:
        raise HTTPException(400, "invalid_status_for_flag")
    repo = SuspiciousPairsRepository(session)
    pair = await repo.flag(
        a=payload.membership_id_a,
        b=payload.membership_id_b,
        reason=payload.reason,
        status=payload.status,
    )
    return ActionResponse(ok=True, status=pair.status)


@router.post("/admin/suspicious_pairs/clear", response_model=ActionResponse)
async def clear_pair(
    payload: ClearRequest,
    session: SessionDep,
    _caller: ServiceCallerDep,
) -> ActionResponse:
    repo = SuspiciousPairsRepository(session)
    deleted = await repo.clear(payload.membership_id_a, payload.membership_id_b)
    if not deleted:
        raise HTTPException(404, "pair_not_found")
    return ActionResponse(ok=True, status="cleared")


@router.post("/admin/suspicious_pairs/ban", response_model=ActionResponse)
async def ban_pair(
    payload: FlagRequest,
    session: SessionDep,
    _caller: ServiceCallerDep,
) -> ActionResponse:
    """Жёсткое действие: пара помечается как banned. Бонусы больше не начисляются."""
    service = SuspiciousPairsService(session)
    pair = await service._repo.ban(  # noqa: SLF001
        a=payload.membership_id_a,
        b=payload.membership_id_b,
        reason=payload.reason,
    )
    return ActionResponse(ok=True, status=pair.status)

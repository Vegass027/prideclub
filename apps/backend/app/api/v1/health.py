from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.core.deps import RedisDep, SessionDep

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: SessionDep, redis: RedisDep) -> dict[str, str]:
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        await asyncio.wait_for(redis.ping(), timeout=2.0)
    except (TimeoutError, Exception) as exc:
        raise HTTPException(status_code=503, detail=f"not ready: {exc}") from exc
    return {"status": "ready"}
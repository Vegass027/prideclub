from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.redis import get_redis_dep
from app.db.session import get_session


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    db: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis_dep),
) -> dict[str, str]:
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        await asyncio.wait_for(redis.ping(), timeout=2.0)
    except (asyncio.TimeoutError, Exception) as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail=f"not ready: {exc}") from exc
    return {"status": "ready"}
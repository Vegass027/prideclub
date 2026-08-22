"""Admin API blueprint.

Точка входа для /admin/v1/* — owner-only контур управления клубами
(TZ_kharakteristiki_personazha.md §3.6).

Аутентификация на уровне AuthMiddleware (X-Telegram-Init-Data + owner check).
Здесь только маршрутизация и DI-обвязка сервисов.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.admin.v1 import habits as admin_habits
from app.api.admin.v1 import stat_definitions as admin_stat_definitions  # NEW (Phase 3 v2 Task 3.7)
from app.api.admin.v1 import uploads as admin_uploads

api_router = APIRouter()
api_router.include_router(admin_habits.router)
api_router.include_router(admin_stat_definitions.router)  # NEW
api_router.include_router(admin_uploads.router)


__all__ = ["api_router"]

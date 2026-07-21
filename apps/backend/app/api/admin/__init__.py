"""Admin API blueprint.

Точка входа для /admin/v1/* — owner-only контур управления клубами
(TZ_kharakteristiki_personazha.md §3.6).

Аутентификация на уровне AuthMiddleware (X-Telegram-Init-Data + owner check).
Здесь только маршрутизация и DI-обвязка сервисов.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.admin.v1 import habits as admin_habits


api_router = APIRouter()
api_router.include_router(admin_habits.router)


__all__ = ["api_router"]

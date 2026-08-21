"""Mock-based SQL-contract тесты для StatDefinitionRepository
и UserStatusRepository (Phase 3.2).

Read-only репозитории с тривиальным SQL (1 SELECT в каждом).
Тесты верифицируют только API-contract — реальные read'ы покроются
test_character_service.py в Task 3.3 end-to-end.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories.stat_definition_repository import StatDefinitionRepository
from app.repositories.user_status_repository import UserStatusRepository


def _sql_of(stmt: Any) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class _ScriptedSession:
    def __init__(self, return_script: list[Any]) -> None:
        self.execute_calls: list[Any] = []
        self._script = return_script
        self._idx = 0

    def _next(self) -> Any:
        idx = min(self._idx, len(self._script) - 1)
        self._idx += 1
        return self._script[idx]

    async def execute(self, stmt: Any) -> Any:
        self.execute_calls.append(stmt)
        return self._next()

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


def _empty_scalars_result() -> Any:
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


# ─── StatDefinitionRepository ──────────────────────────────────

@pytest.mark.asyncio
async def test_stat_definition_repository_list_active_filters_and_orders() -> None:
    """list_active: WHERE is_active IS true + ORDER BY sort_order."""
    sess = _ScriptedSession([_empty_scalars_result()])
    repo = StatDefinitionRepository(sess)  # type: ignore[arg-type]

    rows = await repo.list_active()

    assert rows == []
    assert len(sess.execute_calls) == 1
    sql = _sql_of(sess.execute_calls[0])
    sql_lower = sql.lower()
    sql_upper = sql.upper()
    assert sql_upper.startswith("SELECT")
    assert "from stat_definitions" in sql_lower
    assert "is_active" in sql_lower
    assert "is true" in sql_lower  # PostgreSQL: IS TRUE
    assert "order by" in sql_lower
    assert "sort_order" in sql_lower


# ─── UserStatusRepository ─────────────────────────────────────

@pytest.mark.asyncio
async def test_user_status_repository_list_all_ordered_orders_by_sort_order() -> None:
    """list_all_ordered: ORDER BY sort_order ASC (sort_order 1→5)."""
    sess = _ScriptedSession([_empty_scalars_result()])
    repo = UserStatusRepository(sess)  # type: ignore[arg-type]

    rows = await repo.list_all_ordered()

    assert rows == []
    assert len(sess.execute_calls) == 1
    sql = _sql_of(sess.execute_calls[0])
    sql_lower = sql.lower()
    sql_upper = sql.upper()
    assert sql_upper.startswith("SELECT")
    assert "from user_statuses" in sql_lower
    assert "order by" in sql_lower
    assert "sort_order" in sql_lower

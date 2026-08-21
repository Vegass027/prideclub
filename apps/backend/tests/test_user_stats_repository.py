"""Unit-тесты для UserStatsRepository — mock-based.

Зачем mock: SQL-семантика (INSERT ... ON CONFLICT, SELECT FOR UPDATE,
фильтры WHERE, ORDER BY) верифицируется через инспекцию переданных
SQLAlchemy-стейтментов. Реальный row-lock подтверждается:

1. make migrate-test round-trip (Task 3.1 — покрыто).
2. Интеграционными тестами CheckinService/PenaltyService в Task 3.4
   (там появятся реальные tx и race-сценарии).
3. Worker cron test в Task 3.5 — закрытые циклы freeze/unfreeze.

Variant 1 (per Дмитрий 21.08.2026): freeze() НЕ идемпотентен,
идемпотентность проверяется в test_character_service.py
(как test_apply_freeze_idempotent).

Тест на bulk_freeze + iter_for_freeze_cron end-to-end:
«2001 inactive stats → один запуск cron должен заморозить все 2001»
— переехал в Task 3.5 (там реальный цикл freeze/unfreeze).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.user_stats import UserStats
from app.repositories.user_stats_repository import UserStatsRepository


# ─── helpers ────────────────────────────────────────────────────

def _make_stat(
    *,
    id: str | None = None,
    user_id: int = 42,
    stat_definition_id: str = "intel",
    value: int = 0,
    is_frozen: bool = False,
    frozen_at: datetime | None = None,
    frozen_reason_text: str | None = None,
    last_checkin_at: datetime | None = None,
) -> UserStats:
    """Конструирует UserStats без БД для mutation-тестов."""
    return UserStats(
        id=id or str(uuid4()),
        user_id=user_id,
        stat_definition_id=stat_definition_id,
        value=value,
        is_frozen=is_frozen,
        frozen_at=frozen_at,
        frozen_reason_text=frozen_reason_text,
        last_checkin_at=last_checkin_at,
    )


def _sql_of(stmt: Any) -> str:
    """Компилирует SQLAlchemy stmt в PostgreSQL SQL-строку для инспекции.

    Сравниваем substring'и — compile с literal_binds нестабилен по
    whitespace. Главное — наличие ключевых clauses.
    """
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class _ScriptedSession:
    """Async-сессия: возвращает result по индексу из заранее заданного скрипта.

    Для INSERT ON CONFLICT и SELECT FOR UPDATE в одном get_or_create
    вызове — нужны 2 явных return'а: [INSERT_result, SELECT_result].
    Если скрипт короче — повтор последнего.
    """

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

    def add(self, obj: Any) -> None:
        pass


# ─── 1. get_or_create_for_update, success path ────────────────

@pytest.mark.asyncio
async def test_get_or_create_for_update_first_insert_returns_row_created_true() -> None:
    new_row = _make_stat(value=0)
    sess = _ScriptedSession(
        [SimpleNamespace(scalar_one_or_none=lambda: new_row)]
    )
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    row, created = await repo.get_or_create_for_update(
        user_id=42, stat_definition_id="intel"
    )

    assert created is True
    assert row is new_row
    # SQL: INSERT ... ON CONFLICT (user_id, stat_definition_id)
    sql = _sql_of(sess.execute_calls[0])
    assert sql.upper().startswith("INSERT")
    assert "ON CONFLICT" in sql.upper()
    assert "user_id" in sql.lower()
    assert "stat_definition_id" in sql.lower()
    # Только один execute (Insert succeeded, SELECT FOR UPDATE не нужен).
    assert len(sess.execute_calls) == 1


# ─── 2. get_or_create_for_update, conflict path ────────────────

@pytest.mark.asyncio
async def test_get_or_create_for_update_conflict_runs_select_for_update_created_false() -> None:
    """INSERT вернул None (конфликт) — fallback на SELECT FOR UPDATE."""
    existing_row = _make_stat(value=7)
    sess = _ScriptedSession(
        [
            # 1st execute: INSERT ON CONFLICT → None (row already exists)
            SimpleNamespace(scalar_one_or_none=lambda: None),
            # 2nd execute: SELECT FOR UPDATE → existing row
            SimpleNamespace(scalar_one=lambda: existing_row),
        ]
    )
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    row, created = await repo.get_or_create_for_update(
        user_id=42, stat_definition_id="intel"
    )

    assert created is False
    assert row is existing_row
    assert len(sess.execute_calls) == 2

    insert_sql = _sql_of(sess.execute_calls[0])
    assert insert_sql.upper().startswith("INSERT")
    assert "ON CONFLICT" in insert_sql.upper()

    # 2nd execute — SELECT с with_for_update (rendered SQL содержит
    # FOR UPDATE).
    select_sql = _sql_of(sess.execute_calls[1])
    assert select_sql.upper().startswith("SELECT")
    assert "FOR UPDATE" in select_sql.upper()
    assert "user_id" in select_sql.lower()
    assert "stat_definition_id" in select_sql.lower()


# ─── 3. decrement_with_floor ───────────────────────────────────

@pytest.mark.asyncio
async def test_decrement_with_floor_clamps_at_zero() -> None:
    stat = _make_stat(value=1)
    sess = _ScriptedSession([])
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    await repo.decrement_with_floor(stat, loss=5)

    assert stat.value == 0  # floor, не -4
    # decrement_with_floor — pure ORM mutation, никаких execute.


@pytest.mark.asyncio
async def test_decrement_with_floor_logs_when_floored() -> None:
    """Floor в ноль логирует stat_decrement_floored_at_zero.

    ⚠️ `get_logger(name)` возвращает structlog BoundLogger, который
    с `cache_logger_on_first_use=True` НЕ проксируется в pytest
    caplog (рендерится JSONRenderer'ом напрямую в stdout). Поэтому
    подменяем `repo._logger` на собранный stub, перехватывающий
    вызовы напрямую.
    """
    stat = _make_stat(value=1)
    sess = _ScriptedSession([])
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    captured: list[tuple[str, dict]] = []
    repo._logger = _LoggerCapture(captured)  # type: ignore[assignment]

    await repo.decrement_with_floor(stat, loss=5)

    assert stat.value == 0
    assert any(
        msg == "stat_decrement_floored_at_zero"
        for msg, _ in captured
    ), (
        f"expected stat_decrement_floored_at_zero, got: "
        f"{[m for m, _ in captured]}"
    )


# ─── 4. increment_value defensive ──────────────────────────────

class _LoggerCapture:
    """Stub-логгер: записывает (msg, extra) в provided list.

    Подменяет structlog BoundLogger для тестов. НЕ проксирует через
    stdlib logging — pytest caplog не видит structlog при включённом
    JSONRenderer (см. app/core/logging.py:23).
    """

    def __init__(self, sink: list[tuple[str, dict]]) -> None:
        self._sink = sink

    def info(self, msg: str, extra: dict | None = None) -> None:
        self._sink.append((msg, extra or {}))

    def warning(self, msg: str, extra: dict | None = None) -> None:
        self._sink.append((msg, extra or {}))

    def error(self, msg: str, extra: dict | None = None) -> None:
        self._sink.append((msg, extra or {}))

    def debug(self, msg: str, extra: dict | None = None) -> None:
        self._sink.append((msg, extra or {}))


@pytest.mark.asyncio
async def test_increment_value_no_op_on_non_positive_gain() -> None:
    stat = _make_stat(value=10)
    sess = _ScriptedSession([])
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    captured: list[tuple[str, dict]] = []
    repo._logger = _LoggerCapture(captured)  # type: ignore[assignment]

    await repo.increment_value(stat, gain=0)
    await repo.increment_value(stat, gain=-3)

    assert stat.value == 10  # оба no-op
    non_pos_count = sum(
        1 for msg, _ in captured if msg == "non_positive_increment"
    )
    assert non_pos_count == 2


# ─── 5. unfreeze — ВСЕ 4 frozen-связанных поля очищаются ──────

@pytest.mark.asyncio
async def test_unfreeze_clears_all_frozen_fields_and_resets_last_checkin() -> None:
    """is_frozen, frozen_at, frozen_reason_text сбрасываются;
    last_checkin_at обновляется на now().

    ⚠️ frozen_reason_text ОБЯЗАН сбрасываться (иначе старый
    «Характеристика заморожена…» висит в API-ответе рядом с
    активной характеристикой).
    """
    old_frozen_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    stat = _make_stat(
        value=5,
        is_frozen=True,
        frozen_at=old_frozen_at,
        frozen_reason_text="Характеристика заморожена: нет чек-инов более 30 дней",
        last_checkin_at=old_frozen_at,
    )
    sess = _ScriptedSession([])
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    before = datetime.now(tz=timezone.utc)
    await repo.unfreeze(stat)
    after = datetime.now(tz=timezone.utc)

    assert stat.is_frozen is False
    assert stat.frozen_at is None
    assert stat.frozen_reason_text is None
    assert stat.last_checkin_at is not None
    assert before <= stat.last_checkin_at <= after
    # value сохраняется:
    assert stat.value == 5


# ─── 6. touch_last_checkin ────────────────────────────────────

@pytest.mark.asyncio
async def test_touch_last_checkin_sets_current_utc() -> None:
    stat = _make_stat(last_checkin_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    sess = _ScriptedSession([])
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    before = datetime.now(tz=timezone.utc)
    await repo.touch_last_checkin(stat)
    after = datetime.now(tz=timezone.utc)

    assert stat.last_checkin_at is not None
    assert before <= stat.last_checkin_at <= after


# ─── 7. bulk_freeze — rowcount + WHERE-защита ─────────────────

class _Result:
    """Fake SQL result с rowcount."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


@pytest.mark.asyncio
async def test_bulk_freeze_returns_rowcount_and_uses_is_frozen_false_guard() -> None:
    """rowcount = 5 при 5 frozen строках; WHERE содержит is_frozen=false."""
    sess = _ScriptedSession([_Result(rowcount=5)])
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    affected = await repo.bulk_freeze(
        [str(uuid4()) for _ in range(5)],
        reason_text="Характеристика заморожена: нет чек-инов более 30 дней",
    )

    assert affected == 5
    sql = _sql_of(sess.execute_calls[0])
    sql_lower = sql.lower()
    sql_upper = sql.upper()
    assert sql_upper.startswith("UPDATE")
    assert "user_stats" in sql_lower
    # WHERE-защита (defense-in-depth):
    assert "is_frozen" in sql_lower
    assert "false" in sql_lower
    assert "frozen_at" in sql_lower
    assert "frozen_reason_text" in sql_lower


@pytest.mark.asyncio
async def test_bulk_freeze_empty_list_returns_zero_without_execute() -> None:
    sess = _ScriptedSession([])
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    affected = await repo.bulk_freeze([], reason_text="anything")
    assert affected == 0
    assert sess.execute_calls == []  # нет смысла UPDATE по пустому списку


# ─── 8. iter_for_freeze_cron — SQL без OFFSET ─────────────────

@pytest.mark.asyncio
async def test_iter_for_freeze_cron_sql_filters_correctly_no_offset() -> None:
    """Только is_frozen=false AND last_checkin_at NOT NULL AND < threshold.

    ⚠️ OFFSET запрещён (recon Phase 3.2 fix 1): после bulk_freeze
    выпавшие строки исчезают из WHERE, и OFFSET пропустил бы ещё
    не замороженных candidate'ов. Подход — top-N каждый раз заново
    по тому же WHERE + стабильный ORDER BY (stat_definition_id,
    last_checkin_at, id).
    """
    sess = _ScriptedSession(
        [SimpleNamespace(all=lambda: [])]  # empty → generator exits
    )
    repo = UserStatsRepository(sess)  # type: ignore[arg-type]

    agen = repo.iter_for_freeze_cron(threshold_days=30, batch=1000)
    async for _ in agen:  # consume to trigger one execute
        pass

    sql = _sql_of(sess.execute_calls[0])
    sql_lower = sql.lower()
    sql_upper = sql.upper()
    assert sql_upper.startswith("SELECT")
    assert "from user_stats" in sql_lower
    # WHERE-clauses:
    assert "is_frozen" in sql_lower
    assert "false" in sql_lower
    assert "last_checkin_at is not null" in sql_lower
    assert "last_checkin_at <" in sql_lower
    # Стабильный ORDER BY:
    assert "order by" in sql_lower
    assert "stat_definition_id" in sql_lower
    assert "last_checkin_at" in sql_lower
    assert "limit" in sql_lower
    # ⚠️ offset НЕ должно быть (защита от пропуска):
    assert "offset" not in sql_lower, (
        "OFFSET запрещён в iter_for_freeze_cron — будет пропуск "
        "после freeze первого батча"
    )

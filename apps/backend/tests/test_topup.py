"""Integration tests for POST /api/v1/payments/topup (mock).

MVP: до подключения реального платёжного провайдера юзер пополняет
депозит через эту ручку. Идемпотентность mock-charge_id НЕ гарантируется
(каждый вызов уникален) — это норм для мока, на проде придёт webhook
с реальным charge_id и UNIQUE-индекс на transactions.idempotency_key
обеспечит идемпотентность.
"""
from __future__ import annotations

# STATIC_DIR must be set BEFORE importing create_app, because main.py
# calls os.makedirs(_static_dir, exist_ok=True) during create_app()
# invocation at module-level (line 146).
import os
import tempfile

os.environ.setdefault("STATIC_DIR", tempfile.mkdtemp(prefix="hc_topup_static_"))
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")

import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event as _sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.compiler import SQLCompiler

from app.core.config import get_settings
from app.core.constants import ProofType, TransactionType
from app.db import session as session_module
from app.main import create_app
from app.models.habit import Habit
from app.models.membership import Membership, MembershipStatus
from app.models.transaction import Transaction
from app.models.user import User

# --- Postgres → SQLite shims (локально, без зависимости от test_habit_gates) ---


def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return f"'{uuid.uuid4()}'"


def _compile_current_date(_cls, _elem, **_kw):
    return "CURRENT_DATE"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore[attr-defined]
SQLCompiler.visit_current_date = _compile_current_date  # type: ignore[attr-defined]
SQLCompiler.visit_now = _compile_now  # type: ignore[attr-defined]


def _rewrite_sql_for_sqlite(statement, parameters, _uuid_seq):
    import re

    def _repl_uuid(_m):
        _uuid_seq[0] += 1
        return f"'{uuid.uuid4()}'"

    def _repl_now(_m):
        return "CURRENT_TIMESTAMP"

    def _repl_date(_m):
        return "CURRENT_DATE"

    statement = re.sub(r"gen_random_uuid\s*\(\s*\)", _repl_uuid, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bnow\s*\(\s*\)", _repl_now, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bcurrent_date\b", _repl_date, statement, flags=re.IGNORECASE)
    return statement, parameters


def _remap_postgres_types_for_sqlite() -> None:
    from sqlalchemy import JSON, String
    from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

    models = [User, Habit, Membership]
    for m in models:
        for col in m.__table__.columns:
            t = col.type
            if isinstance(t, UUID) and not t.as_uuid:
                col.type = String(36)
            elif isinstance(t, JSONB):
                col.type = JSON()
            elif isinstance(t, INET):
                col.type = String(45)


_remap_postgres_types_for_sqlite()


# --- helpers --------------------------------------------------------------


def _build_init_data(*, user_id: int, bot_token: str = "test-bot-token") -> str:
    user = {"id": user_id, "first_name": "User", "username": "u"}
    params = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "q",
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


# --- fixtures --------------------------------------------------------------


@pytest_asyncio.fixture
async def _sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", "test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("STATIC_DIR", tempfile.mkdtemp(prefix="hc_topup_static_"))
    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_topup_test_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    _uuid_seq: list[int] = [0]

    @_sa_event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def _patch(conn, cursor, statement, parameters, context, executemany):
        return _rewrite_sql_for_sqlite(statement, parameters, _uuid_seq)

    tables = [User.__table__, Habit.__table__, Membership.__table__, Transaction.__table__]
    async with engine.begin() as conn:
        for tbl in tables:
            await conn.run_sync(tbl.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine  # noqa: SLF001
    session_module._session_factory = factory  # noqa: SLF001

    yield engine, factory

    await engine.dispose()
    session_module._engine = None  # noqa: SLF001
    session_module._session_factory = None  # noqa: SLF001
    os.unlink(db_path)
    os.rmdir(tmp_dir)
    get_settings.cache_clear()


@pytest.fixture
def app(_sqlite_engine: Any):
    _, _ = _sqlite_engine
    return create_app()


async def _seed_member(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    habit_id: str,
    deposit_balance: int = 0,
) -> None:
    async with factory() as s:
        s.add(
            Membership(
                id=str(uuid.uuid4()),
                user_id=user_id,
                habit_id=habit_id,
                status=MembershipStatus.ACTIVE,
                deposit_balance=deposit_balance,
                joined_at=datetime.now(UTC),
            )
        )
        await s.commit()


async def _seed_habit(
    factory: async_sessionmaker[AsyncSession],
    *,
    chat_id: int = -1001,
) -> str:
    habit_id = str(uuid.uuid4())
    async with factory() as s:
        s.add(
            Habit(
                id=habit_id,
                title="Test",
                chat_id=chat_id,
                checkin_window_start=datetime.now().time(),
                checkin_window_end=datetime.now().time(),
                timezone="Europe/Moscow",
                penalty_amount=100,
                price_month=1000,
                proof_type=ProofType.VIDEO_NOTE,
                proof_types=[ProofType.VIDEO_NOTE.value],
                is_active=True,
            )
        )
        await s.commit()
    return habit_id


# --- tests -----------------------------------------------------------------


def test_topup_increases_deposit_balance(
    app: Any,
    _sqlite_engine: Any,
) -> None:
    """Happy path: 299 ₽ пополнения → новый баланс, новая transaction."""
    _, factory = _sqlite_engine
    habit_id = asyncio.run(_seed_habit(factory))
    asyncio.run(_seed_member(factory, user_id=123, habit_id=habit_id, deposit_balance=500))

    with TestClient(app) as client:
        r = client.post(
            "/api/v1/payments/topup",
            headers={"X-Telegram-Init-Data": _build_init_data(user_id=123)},
            json={"habit_id": habit_id, "amount_kopecks": 29_900},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["new_deposit_balance"] == 29_900 + 500
    assert body["transaction_id"] is not None

    from sqlalchemy import select

    async def _check_tx() -> None:
        async with factory() as s:
            txs = (await s.execute(select(Transaction))).scalars().all()
            assert len(txs) == 1
            tx = txs[0]
            assert tx.type == TransactionType.DEPOSIT_TOPUP.value
            assert tx.amount == 29_900
            assert tx.balance_after == 29_900 + 500
            assert tx.idempotency_key.startswith("mock:")

    asyncio.run(_check_tx())


def test_topup_rejects_zero_amount(
    app: Any,
    _sqlite_engine: Any,
) -> None:
    """Field(gt=0) → 422 на amount_kopecks=0."""
    _, factory = _sqlite_engine
    habit_id = asyncio.run(_seed_habit(factory))
    asyncio.run(_seed_member(factory, user_id=124, habit_id=habit_id))

    with TestClient(app) as client:
        r = client.post(
            "/api/v1/payments/topup",
            headers={"X-Telegram-Init-Data": _build_init_data(user_id=124)},
            json={"habit_id": habit_id, "amount_kopecks": 0},
        )

    assert r.status_code == 422


def test_topup_without_init_data_returns_401(app: Any) -> None:
    """Без initData → middleware 401."""
    with TestClient(app) as client:
        r = client.post(
            "/api/v1/payments/topup",
            json={"habit_id": "x", "amount_kopecks": 100},
        )

    assert r.status_code == 401
    assert r.json()["code"] == "missing_init_data"

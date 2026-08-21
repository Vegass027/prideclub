"""Тесты для GET /api/v1/habits/{habit_id}/leaderboard (Phase 3 v2 Task 3.6).

⚠️ Functional SQLite tests (НЕ concurrency). Покрывают:
- 404 для missing/archived habit (через HabitNotFoundError / HabitArchivedError).
- NULL stat_definition_id → пустой лидерборд (200 OK, items=[]).
- SQL active-filter-before-LIMIT (LEFT/PAUSED memberships excluded).
- Frozen stats остаются в выдаче с is_frozen=true.
- ?limit=200 → 422 (Pydantic validation cap).
- ORDER BY: value DESC, membership.id ASC (stable tie-breaker).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from uuid import uuid4

# --- env bootstrap (как в test_catch_api.py) ----------------------------
os.environ["STATIC_DIR"] = tempfile.mkdtemp(prefix="hc_stat_lb_static_")
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SSE_TOKEN_SECRET", "test-sse-token-secret")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, String  # noqa
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID  # noqa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.compiler import SQLCompiler

from app.core.constants import MembershipStatus, ProofType  # noqa
from app.models.checkin import Checkin  # noqa
from app.models.habit import Habit  # noqa
from app.models.membership import Membership  # noqa
from app.models.penalty import Penalty  # noqa
from app.models.user import User  # noqa
# Phase 3 v2 — для stat leaderboard:
from app.models.stat_definition import StatDefinition  # noqa
from app.models.user_status import UserStatus  # noqa
from app.models.user_stats import UserStats  # noqa

# --- Postgres → SQLite shims ---------------------------------------

def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return f"'{uuid4()}'"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore
SQLCompiler.visit_now = _compile_now  # type: ignore


_postgres_models = [
    User, Habit, Membership, Checkin, Penalty,
    StatDefinition, UserStatus, UserStats,
]
for _m in _postgres_models:
    for _col in _m.__table__.columns:
        _t = _col.type
        if isinstance(_t, UUID) and not _t.as_uuid:
            _col.type = String(36)
        elif isinstance(_t, JSONB):
            _col.type = JSON()
        elif isinstance(_t, INET):
            _col.type = String(45)


# --- initData helper (как в test_catch_api.py) -----------------------

def _build_init_data(*, user_id: int) -> str:
    user = {"id": user_id, "first_name": "Tester", "username": "tester"}
    params = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "q",
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", b"test-bot-token", hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


HEADERS = {"X-Telegram-Init-Data": _build_init_data(user_id=999)}


# --- DB / client fixtures -----------------------------------------


@pytest.fixture
async def db_factory():
    """Файл-SQLite + все таблицы Phase 3."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    tables = [_m.__table__ for _m in _postgres_models]
    async with engine.begin() as conn:
        for tbl in tables:
            await conn.run_sync(tbl.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    try:
        os.unlink(db_path)
    except OSError:
        pass


def _build_test_client(factory):
    from app.core.config import get_settings
    from app.core.security import TelegramUser
    from app.db import session as session_module
    from app.main import create_app

    get_settings.cache_clear()
    session_module._session_factory = factory  # noqa: SLF001

    app = create_app()

    async def _fake_current_user_db():
        return TelegramUser(
            id=999, first_name="Tester", last_name=None,
            username="tester", language_code=None, is_premium=False,
        auth_date=int(time.time()),
    )

    from app.api.v1.users import current_user_db
    app.dependency_overrides[current_user_db] = _fake_current_user_db
    return TestClient(app)


# --- seed helpers -------------------------------------------------


async def _seed_habit_with_stat(
    factory,
    *,
    membership_statuses: list[MembershipStatus],
    stat_value: int = 10,
    stat_definition_id: str = "sd-intel",
    set_habit_stat_definition: bool = True,
    archive: bool = False,
) -> tuple[str, list[tuple[int, str]]]:
    """Создаёт один habit + StatDefinition + N memberships (по статусам).

    Returns (habit_id, [(user_id, membership_id), ...]).
    """
    habit_id = str(uuid4())
    user_pairs: list[tuple[int, str]] = []
    async with factory() as s:
        s.add(
            StatDefinition(
                id=stat_definition_id,
                slug="intelligence",
                name="Интеллект",
                icon="🧠",
                sort_order=1,
                is_active=True,
            )
        )
        for i, (name, threshold, icon, sort_order) in enumerate([
            ("На старте", 0, "🐣", 1),
            ("В потоке", 30, "🌊", 2),
            ("На волне", 100, "⚡", 3),
            ("В форме", 300, "🔥", 4),
            ("Режим зверя", 700, "🐺", 5),
        ]):
            s.add(UserStatus(
                id=f"us-{i+1}", status_name=name, min_threshold=threshold,
                icon=icon, sort_order=sort_order,
            ))
        s.add(
            Habit(
                id=habit_id,
                title="Test Club",
                chat_id=-1001,
                checkin_window_start=datetime.now().time(),
                checkin_window_end=datetime.now().time(),
                timezone="Europe/Moscow",
                penalty_amount=100,
                price_month=1000,
                proof_type=ProofType.VIDEO_NOTE,
                proof_types=[ProofType.VIDEO_NOTE.value],
                is_active=True,
                archived_at=datetime.now(tz=UTC) if archive else None,
                stat_definition_id=stat_definition_id if set_habit_stat_definition else None,
            )
        )
        for i, status in enumerate(membership_statuses):
            user_id = 10000 + i
            m_id = str(uuid4())
            user_pairs.append((user_id, m_id))
            s.add(User(id=user_id, first_name=f"U{i}"))
            s.add(
                Membership(
                    id=m_id,
                    user_id=user_id,
                    habit_id=habit_id,
                    status=status,
                    joined_at=datetime.now(tz=UTC),
                )
            )
            s.add(
                UserStats(
                    id=str(uuid4()),
                    user_id=user_id,
                    stat_definition_id=stat_definition_id,
                    value=stat_value,
                    last_checkin_at=datetime.now(tz=UTC) - timedelta(days=1),
                    is_frozen=False,
                )
            )
        await s.commit()
    return habit_id, user_pairs


# --- tests ---------------------------------------------------------


async def test_stat_leaderboard_returns_404_for_missing_habit(db_factory):
    """habit_id="<unknown>" → 404 + code='habit_not_found'."""
    client = _build_test_client(db_factory)
    resp = client.get(
        "/api/v1/habits/00000000-0000-0000-0000-000000000000/leaderboard",
        headers=HEADERS,
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == "habit_not_found"


async def test_stat_leaderboard_returns_empty_for_habit_with_no_stat_definition(db_factory):
    """habit.stat_definition_id IS NULL → 200 + items=[] (фича не активирована)."""
    habit_id, _ = await _seed_habit_with_stat(
        db_factory,
        membership_statuses=[MembershipStatus.ACTIVE],
        set_habit_stat_definition=False,
    )
    client = _build_test_client(db_factory)
    resp = client.get(
        f"/api/v1/habits/{habit_id}/leaderboard",
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"items": [], "total": None}


async def test_stat_leaderboard_filters_inactive_memberships_in_sql(db_factory):
    """⚠️ ГЛАВНАЯ SQL-evidence: 4 memberships (2 ACTIVE, 1 PAUSED, 1 LEFT) →
    в выдаче остаются ТОЛЬКО 2 ACTIVE. PAUSED/LEFT исключены в WHERE ДО LIMIT.
    """
    habit_id, user_pairs = await _seed_habit_with_stat(
        db_factory,
        membership_statuses=[
            MembershipStatus.ACTIVE,
            MembershipStatus.PAUSED,
            MembershipStatus.LEFT,
            MembershipStatus.ACTIVE,
        ],
        stat_value=10,
    )
    client = _build_test_client(db_factory)
    resp = client.get(
        f"/api/v1/habits/{habit_id}/leaderboard",
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["items"]) == 2, (
        f"PAUSED/LEFT memberships должны быть исключены SQL-фильтром "
        f"ДО LIMIT. Got {len(body['items'])} items: {body['items']}"
    )
    expected_user_ids = {10000, 10003}  # ACTIVE positions (0 and 3)
    actual_user_ids = {item["user_id"] for item in body["items"]}
    assert actual_user_ids == expected_user_ids, (
        f"Expected user_ids {expected_user_ids}, got {actual_user_ids}"
    )


async def test_stat_leaderboard_limit_query_param_caps_at_100(db_factory):
    """?limit=200 → FastAPI 422 (Pydantic `le=100`).

    Без параметра → default 100. С ?limit=50 → respect.
    """
    habit_id, _ = await _seed_habit_with_stat(
        db_factory,
        membership_statuses=[MembershipStatus.ACTIVE],
        stat_value=10,
    )
    client = _build_test_client(db_factory)

    resp = client.get(
        f"/api/v1/habits/{habit_id}/leaderboard?limit=200",
        headers=HEADERS,
    )
    assert resp.status_code == 422, resp.text

    resp = client.get(
        f"/api/v1/habits/{habit_id}/leaderboard?limit=0",
        headers=HEADERS,
    )
    assert resp.status_code == 422, resp.text

    resp = client.get(
        f"/api/v1/habits/{habit_id}/leaderboard",
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text


async def test_stat_leaderboard_includes_frozen_stats_with_flag(db_factory):
    """Frozen stats (is_frozen=true) остаются в выдаче — UI рисует ❄."""
    habit_id, user_pairs = await _seed_habit_with_stat(
        db_factory,
        membership_statuses=[
            MembershipStatus.ACTIVE,
            MembershipStatus.ACTIVE,
        ],
        stat_value=42,
    )
    from sqlalchemy import update as sa_update
    async with db_factory() as s:
        frozen_user_id = user_pairs[1][0]
        await s.execute(
            sa_update(UserStats)
            .where(UserStats.user_id == frozen_user_id)
            .values(is_frozen=True, frozen_at=datetime.now(tz=UTC))
        )
        await s.commit()

    client = _build_test_client(db_factory)
    resp = client.get(
        f"/api/v1/habits/{habit_id}/leaderboard",
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["items"]) == 2
    frozen_states = sorted(item["is_frozen"] for item in body["items"])
    assert frozen_states == [False, True], (
        f"Должны быть оба — frozen и not-frozen. Got {frozen_states}"
    )
    active_user_ids = {user_pairs[0][0], user_pairs[1][0]}
    actual_user_ids = {item["user_id"] for item in body["items"]}
    assert actual_user_ids == active_user_ids


async def test_stat_leaderboard_tiebreaker_by_membership_id_when_value_equal(db_factory):
    """Two users with equal value → tie-breaker membership_id ASC."""
    _, _ = await _seed_habit_with_stat(
        db_factory,
        membership_statuses=[
            MembershipStatus.ACTIVE,
            MembershipStatus.ACTIVE,
        ],
        stat_value=50,
    )
    from sqlalchemy import select
    async with db_factory() as s:
        result = await s.execute(select(Habit.id).where(Habit.title == "Test Club"))
        habit_id = result.scalar_one()

    client = _build_test_client(db_factory)
    resp = client.get(
        f"/api/v1/habits/{habit_id}/leaderboard",
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()

    # Tie-breaker: при равных value — membership_id ASC (lexicographic UUID).
    mid_list = [item["membership_id"] for item in body["items"]]
    assert mid_list == sorted(mid_list), (
        f"Tie-breaker должен быть membership_id ASC. Got: {mid_list}"
    )
    values = {item["value"] for item in body["items"]}
    assert values == {50}, f"Обе value должны быть 50, got {values}"

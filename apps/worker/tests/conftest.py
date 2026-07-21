"""Pytest-фикстуры для worker-тестов.

Поднимаем in-memory SQLite с реальными backend-моделями (таблицы, индексы, ENUM'ы
через type_compiler) и подменяем `apps.worker.db.session.async_session_factory`
на нашу фабрику. По официальной доке Celery:

> A Celery task should ideally focus on serialization, message headers, and
> retries, with the core logic implemented elsewhere.

Все worker-таски в этом проекте уже разнесены: бизнес-логика в чистых
async-функциях (`run_for_active_habits`, `run`, `_process`, …) — тестируем
именно их.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from sqlalchemy.sql.compiler import SQLCompiler

import db.session as _worker_db_session  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "apps" / "backend"
SHARED_ROOT = REPO_ROOT / "packages" / "shared"
WORKER_ROOT = REPO_ROOT / "apps" / "worker"

for _p in (str(BACKEND_ROOT), str(SHARED_ROOT), str(WORKER_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Минимальные env-переменные для Settings() из backend и worker.config."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", "test-service-secret")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return "'00000000-0000-0000-0000-000000000000'"


def _compile_current_date(_cls, _elem, **_kw):
    return "CURRENT_DATE"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore[attr-defined]
SQLCompiler.visit_current_date = _compile_current_date  # type: ignore[attr-defined]
SQLCompiler.visit_now = _compile_now  # type: ignore[attr-defined]


def _remap_postgres_types_for_sqlite() -> None:
    """SQLite не умеет UUID/JSONB/INET из PostgreSQL — заменяем их на String/JSON.

    Это касается только тестовых таблиц. В проде (Postgres) ничего не меняется.
    """
    from sqlalchemy import JSON, String
    from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

    from app.models.auxiliary import (
        BonusRule,
        DailyStreakSnapshot,
        SeasonPrizeRule,
        SuspiciousPair,
    )
    from app.models.checkin import Checkin
    from app.models.habit import Habit
    from app.models.membership import Membership
    from app.models.penalty import Penalty
    from app.models.season import Season, SeasonStats
    from app.models.transaction import Transaction
    from app.models.user import User

    models = [
        User,
        Habit,
        Membership,
        Checkin,
        Penalty,
        Transaction,
        Season,
        SeasonStats,
        DailyStreakSnapshot,
        SuspiciousPair,
        BonusRule,
        SeasonPrizeRule,
    ]
    for m in models:
        for col in m.__table__.columns:
            t = col.type
            if isinstance(t, UUID) and not t.as_uuid:
                col.type = String(36)
            elif isinstance(t, JSONB):
                col.type = JSON()
            elif isinstance(t, INET):
                col.type = String(45)  # IPv6 max length


_remap_postgres_types_for_sqlite()


@pytest_asyncio.fixture
async def worker_db(monkeypatch):
    """In-memory SQLite + реальные таблицы backend-моделей + session_factory.

    Подменяет `_worker_db_session.async_session_factory` и `.engine` так, чтобы
    worker-таски (`from db.session import async_session_factory`) ходили в тестовую БД.

    Возвращает объект с полями:
        - engine: AsyncEngine
        - session_factory: async_sessionmaker[AsyncSession]
        - add_user, add_habit, add_membership, add_checkin, add_penalty, add_transaction,
          add_season, add_season_stats, add_bonus_rule — хелперы для сидинга.
    """
    from app.models.auxiliary import (
        BonusRule,
        DailyStreakSnapshot,
        SeasonPrizeRule,
        SuspiciousPair,
    )
    from app.models.checkin import Checkin
    from app.models.habit import Habit
    from app.models.membership import Membership
    from app.models.penalty import Penalty
    from app.models.season import Season, SeasonStats
    from app.models.transaction import Transaction
    from app.models.user import User
    from datetime import date, datetime
    from uuid import uuid4

    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    tables = [
        User.__table__,
        Habit.__table__,
        Membership.__table__,
        Checkin.__table__,
        Penalty.__table__,
        Transaction.__table__,
        Season.__table__,
        SeasonStats.__table__,
        DailyStreakSnapshot.__table__,
        SuspiciousPair.__table__,
        BonusRule.__table__,
        SeasonPrizeRule.__table__,
    ]

    async with test_engine.begin() as conn:
        for tbl in tables:
            await conn.run_sync(tbl.create)

    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    monkeypatch.setattr(_worker_db_session, "async_session_factory", factory)
    monkeypatch.setattr(_worker_db_session, "engine", test_engine)

    class _Fixture:
        engine = test_engine
        session_factory = factory

        @staticmethod
        async def add_user(
            session: AsyncSession,
            *,
            id: int | None = None,
            first_name: str = "Test",
            username: str | None = "tester",
            timezone_name: str = "Europe/Moscow",
            bonus_points: int = 0,
            bonus_points_updated_at: datetime | None = None,
        ) -> User:
            u = User(
                id=id if id is not None else 1000 + (id or 0),
                first_name=first_name,
                username=username,
                timezone=timezone_name,
                bonus_points=bonus_points,
                bonus_points_updated_at=bonus_points_updated_at,
            )
            session.add(u)
            await session.flush()
            return u

        @staticmethod
        async def add_habit(
            session: AsyncSession,
            *,
            id: str | None = None,
            title: str = "Plank Club",
            chat_id: int = -1001,
            timezone_name: str = "Europe/Moscow",
            penalty_amount: int = 100,
            price_month: int = 1000,
            prize_pool: int = 0,
            checkin_window_start_hour: int = 7,
            checkin_window_end_hour: int = 10,
            is_active: bool = True,
            proof_type=None,
        ) -> Habit:
            from datetime import time

            from app.core.constants import ProofType

            h = Habit(
                id=id or str(uuid4()),
                title=title,
                chat_id=chat_id,
                checkin_window_start=time(checkin_window_start_hour, 0),
                checkin_window_end=time(checkin_window_end_hour, 0),
                timezone=timezone_name,
                penalty_amount=penalty_amount,
                price_month=price_month,
                proof_type=proof_type or ProofType.VIDEO_NOTE,
                prize_pool=prize_pool,
                is_active=is_active,
            )
            session.add(h)
            await session.flush()
            return h

        @staticmethod
        async def add_membership(
            session: AsyncSession,
            *,
            user_id: int,
            habit_id: str,
            id: str | None = None,
            deposit_balance: int = 1000,
            status=None,
        ) -> Membership:
            from app.core.constants import MembershipStatus

            m = Membership(
                id=id or str(uuid4()),
                user_id=user_id,
                habit_id=habit_id,
                status=status or MembershipStatus.ACTIVE,
                deposit_balance=deposit_balance,
            )
            session.add(m)
            await session.flush()
            return m

        @staticmethod
        async def add_checkin(
            session: AsyncSession,
            *,
            membership_id: str,
            on_date: date,
            proof_message_id: int | None = 100,
        ) -> Checkin:
            from app.core.constants import CheckinStatus

            c = Checkin(
                id=str(uuid4()),
                membership_id=membership_id,
                date=on_date,
                status=CheckinStatus.DONE,
                proof_message_id=proof_message_id,
            )
            session.add(c)
            await session.flush()
            return c

        @staticmethod
        async def add_penalty(
            session: AsyncSession,
            *,
            violator_membership_id: str,
            catcher_membership_id: str | None,
            amount: int = 100,
            fund_share: int = 100,
            reason: str,
            on_date: date,
            bonus_applied: bool = False,
        ) -> Penalty:
            p = Penalty(
                id=str(uuid4()),
                membership_id=violator_membership_id,
                catcher_membership_id=catcher_membership_id,
                amount=amount,
                fund_share=fund_share,
                reason=reason,
                date=on_date,
                bonus_applied=bonus_applied,
            )
            session.add(p)
            await session.flush()
            return p

        @staticmethod
        async def add_transaction(
            session: AsyncSession,
            *,
            user_id: int,
            type: str,
            amount: int,
            related_penalty_id: str | None = None,
            related_membership_id: str | None = None,
            balance_after: int | None = None,
        ) -> Transaction:
            tx = Transaction(
                id=str(uuid4()),
                user_id=user_id,
                type=type,
                amount=amount,
                related_penalty_id=related_penalty_id,
                related_membership_id=related_membership_id,
                balance_after=balance_after,
            )
            session.add(tx)
            await session.flush()
            return tx

        @staticmethod
        async def add_season(
            session: AsyncSession,
            *,
            habit_id: str,
            starts_at: date,
            ends_at: date,
            prize_pool: int = 0,
            prize_rules_snapshot: dict | None = None,
            status: str = "active",
        ) -> Season:
            s = Season(
                id=str(uuid4()),
                habit_id=habit_id,
                starts_at=starts_at,
                ends_at=ends_at,
                prize_pool=prize_pool,
                prize_rules_snapshot=prize_rules_snapshot,
                status=status,
            )
            session.add(s)
            await session.flush()
            return s

        @staticmethod
        async def add_season_stats(
            session: AsyncSession,
            *,
            season_id: str,
            membership_id: str,
            streak_days: int = 0,
            total_penalties_caught: int = 0,
            total_penalties_received: int = 0,
        ) -> SeasonStats:
            st = SeasonStats(
                season_id=season_id,
                membership_id=membership_id,
                streak_days=streak_days,
                total_penalties_caught=total_penalties_caught,
                total_penalties_received=total_penalties_received,
            )
            session.add(st)
            await session.flush()
            return st

        @staticmethod
        async def add_bonus_rule(
            session: AsyncSession,
            *,
            event_type: str,
            threshold: int,
            reward_type: str,
            reward_value: int,
        ) -> BonusRule:
            r = BonusRule(
                id=str(uuid4()),
                event_type=event_type,
                threshold=threshold,
                reward_type=reward_type,
                reward_value=reward_value,
            )
            session.add(r)
            await session.flush()
            return r

    yield _Fixture()

    await test_engine.dispose()

"""Тесты для /internal/checkins/process (Pravki §Z-22 hole #1).

Сценарий: enqueue_checkin должен SYNCHRONOUSLY отвергнуть чек-ин если окно
чек-ина закрыто (defense-in-depth pattern, симметрично Item 4 — caught_today).

Бот pre-filter обычно ловит раньше (state.is_within_checkin_window), но
если бот bypassed / старая версия / прямой вызов — backend режет синхронно,
чтобы бот не успел ответить "Принято" и юзер не ждал ложно.

Используем SQLite + StaticPool тот же паттерн, что в test_internal_habit_state.py.
"""
from __future__ import annotations

import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from datetime import time as dt_time
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event as _sa_event
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.compiler import SQLCompiler  # noqa: E402

from app.core.config import get_settings
from app.core.security import generate_service_token
from app.db import session as session_module
from app.main import create_app
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.user import User


def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return "'00000000-0000-0000-0000-000000000000'"


def _compile_current_date(_cls, _elem, **_kw):
    return "CURRENT_DATE"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore[attr-defined]
SQLCompiler.visit_current_date = _compile_current_date  # type: ignore[attr-defined]
SQLCompiler.visit_now = _compile_now  # type: ignore[attr-defined]


def _rewrite_sql_for_sqlite(statement: str, parameters, _uuid_seq: list[int]):
    def _repl_uuid(_m: re.Match) -> str:
        _uuid_seq[0] += 1
        return f"'{uuid.uuid4()}'"

    def _repl_now(_m: re.Match) -> str:
        return "CURRENT_TIMESTAMP"

    def _repl_date(_m: re.Match) -> str:
        return "CURRENT_DATE"

    statement = re.sub(r"gen_random_uuid\s*\(\s*\)", _repl_uuid, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bnow\s*\(\s*\)", _repl_now, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bcurrent_date\b", _repl_date, statement, flags=re.IGNORECASE)
    return statement, parameters


def _remap_postgres_types_for_sqlite() -> None:
    from sqlalchemy import JSON, String
    from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

    for m in (User, Habit):
        for col in m.__table__.columns:
            t = col.type
            if isinstance(t, UUID) and not t.as_uuid:
                col.type = String(36)
            elif isinstance(t, JSONB):
                col.type = JSON()
            elif isinstance(t, INET):
                col.type = String(45)


_remap_postgres_types_for_sqlite()


SERVICE_SECRET = "test-service-secret"


@pytest_asyncio.fixture
async def _sqlite_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "0")
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("SERVICE_SECRET", SERVICE_SECRET)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
    monkeypatch.setenv("REDIS_URL", "")
    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_checkins_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    _uuid_seq: list[int] = [0]

    @_sa_event.listens_for(engine.sync_engine, "before_cursor_execute", retval=True)
    def _patch_sql(conn, cursor, statement, parameters, context, executemany):
        return _rewrite_sql_for_sqlite(statement, parameters, _uuid_seq)

    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(Habit.__table__.create)
        await conn.run_sync(Membership.__table__.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine  # noqa: SLF001
    session_module._session_factory = factory  # noqa: SLF001

    yield engine

    await engine.dispose()
    session_module._engine = None  # noqa: SLF001
    session_module._session_factory = None  # noqa: SLF001
    os.unlink(db_path)
    os.rmdir(tmp_dir)
    get_settings.cache_clear()


@pytest.fixture
def app(_sqlite_engine: Any):
    return create_app()


@pytest.fixture
def service_token() -> str:
    return generate_service_token(
        service_name="bot",
        target_audience="backend-api",
        secret=SERVICE_SECRET,
        ttl_seconds=60,
    )


async def _make_user(s, telegram_id: int) -> User:
    u = User(id=telegram_id, first_name="Probe", username=None)
    s.add(u)
    await s.flush()
    return u


async def _make_membership(s, *, user_id: int, habit_id: Any, status: str) -> Membership:
    """Helper для Шага 3 — создаёт Membership с произвольным status."""
    m = Membership(user_id=user_id, habit_id=habit_id, status=status)
    s.add(m)
    await s.flush()
    return m


async def _make_habit(
    s,
    *,
    chat_id: int,
    checkin_window_start: dt_time,
    checkin_window_end: dt_time,
    checkin_topic_thread_id: int | None = None,
) -> Habit:
    from app.core.constants import ProofType

    h = Habit(
        title="Тест",
        description="Тест",
        chat_id=chat_id,
        checkin_window_start=checkin_window_start,
        checkin_window_end=checkin_window_end,
        timezone="Europe/Moscow",
        penalty_amount=100_00,
        price_month=100_00,
        proof_type=ProofType.VIDEO_NOTE,
        proof_types=["video_note"],
        prize_pool=0,
        is_active=True,
        chat_topic_thread_id=None,
        checkin_topic_thread_id=checkin_topic_thread_id,
    )
    s.add(h)
    await s.flush()
    return h


def _payload_chat_id(chat_id: int, message_thread_id: int | None = None) -> dict[str, Any]:
    return {
        "user_id": 7295309649,
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
        "proof_type": "video_note",
        "message_id": 12345,
        "message_sent_at": datetime.now(tz=UTC).isoformat(),
        "text": None,
        "duration_seconds": 5,
    }


# --- tests ------------------------------------------------------------------


class TestEnqueueCheckinWindowClosed:
    """Pravki §Z-22 (hole #1): synchronous reject when window closed."""

    def test_window_closed_returns_synchronous_reject(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Окно 00:00-00:00:01 (технически уже закрыто) → ok=False,
        code="checkin_window_closed", window_start/end заполнены.

        send_task НЕ вызывается (никаких send_task, никаких Celery).
        """
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1004348250990,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(0, 0, 1),
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="active"
                )
                await s.commit()
            return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "checkin_window_closed"
        assert body["window_start"] == "00:00"
        assert body["window_end"] == "00:00"
        # Главный инвариант: Celery НЕ вызвался
        mock_send.assert_not_called()

    def test_within_window_enqueues_normally(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Окно 00:00-23:59:59 (открыто в ~99.99% случаев) → ok=True, task_id задан.

        send_task ВЫЗЫВАЕТСЯ нормально (с правильными параметрами).
        """
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000001,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="active"
                )
                await s.commit()
            return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch(
            "app.api.v1.internal_checkins.send_task", return_value="task-xyz"
        ) as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "task-xyz"
        # Celery ВЫЗВАЛСЯ
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args.args[0] == "checkin"
        assert call_args.args[1]["user_id"] == 7295309649
        assert call_args.args[1]["chat_id"] == -1000000000001

    def test_habit_not_found_returns_synchronous_reject(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Клуб не существует → ok=False, code=habit_not_found, send_task НЕ вызван."""
        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    # chat_id которого НЕТ в БД
                    json=_payload_chat_id(-1009999999999),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "habit_not_found"
        mock_send.assert_not_called()


class TestEnqueueCheckinWrongTopic:
    """Pravki §Z-22 (hole #2): synchronous reject when wrong topic."""

    def test_wrong_topic_returns_synchronous_reject(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """habit.checkin_topic_thread_id=42, payload.message_thread_id=99
        → ok=False, code="not_checkin_topic", send_task НЕ вызван.
        """
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000002,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                    checkin_topic_thread_id=42,
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="active"
                )
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id, message_thread_id=99),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "not_checkin_topic"
        mock_send.assert_not_called()

    def test_correct_topic_enqueues_normally(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Thread_id совпадает → ok=True, send_task ВЫЗВАЛСЯ."""
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000003,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                    checkin_topic_thread_id=42,
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="active"
                )
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch(
            "app.api.v1.internal_checkins.send_task", return_value="task-abc"
        ) as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id, message_thread_id=42),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "task-abc"
        mock_send.assert_called_once()

    def test_no_topic_thread_id_legacy_accepts_any(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """habit.checkin_topic_thread_id IS NULL (legacy pre-migration 010):
        любой message_thread_id (включая None для General) принимается.
        """
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000004,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                    checkin_topic_thread_id=None,
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="active"
                )
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch(
            "app.api.v1.internal_checkins.send_task", return_value="task-legacy"
        ):
            with TestClient(app) as client:
                # message_thread_id в General (None) — тоже принимается
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id, message_thread_id=None),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "task-legacy"


class TestEnqueueCheckinMembershipStatus:
    """Pravki §Z-22 (Step 3, hole #3): synchronous reject for paused/left/not_found."""

    def test_membership_paused_returns_synchronous_reject(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Membership.status=paused → ok=False, code="membership_paused",
        send_task НЕ вызван.
        """
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000005,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="paused"
                )
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "membership_paused"
        mock_send.assert_not_called()

    def test_membership_left_returns_synchronous_reject(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Membership.status=left → ok=False, code="membership_left",
        send_task НЕ вызван.
        """
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000006,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="left"
                )
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "membership_left"
        mock_send.assert_not_called()

    def test_membership_not_found_returns_synchronous_reject(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Нет membership для (user, habit) → ok=False, code="membership_not_found",
        send_task НЕ вызван.
        """
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000007,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                # Намеренно НЕ создаём membership
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "membership_not_found"
        mock_send.assert_not_called()

    def test_membership_active_enqueues_normally(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Happy path: status=active → ok=True, send_task ВЫЗВАЛСЯ."""
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000008,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="active"
                )
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch(
            "app.api.v1.internal_checkins.send_task", return_value="task-ok"
        ) as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "task-ok"
        mock_send.assert_called_once()

    def test_subscription_expired_returns_synchronous_reject(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Pravki-subscription-2026-08-17 §Z-22 (canonical #6):
        membership.subscription_until < club_date → ok=False, code="subscription_expired".
        send_task НЕ вызван.

        Сравнение по club_date в TZ клуба (Europe/Moscow), без grace period.
        subscription_until = today (в Москве) — последний валидный день.
        subscription_until = yesterday — истекла → reject.
        """
        import asyncio
        from datetime import timedelta

        async def _seed_expired():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000090,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                # Вчера по Москве → подписка точно истекла.
                sub_until = habit.club_date(datetime.now(tz=UTC)) - timedelta(days=1)
                m = Membership(
                    user_id=user.id, habit_id=habit.id,
                    status="active", subscription_until=sub_until,
                )
                s.add(m)
                await s.flush()
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed_expired())

        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "subscription_expired"
        mock_send.assert_not_called()

    def test_subscription_today_last_day_enqueues_normally(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Pravki-subscription-2026-08-17 Q2: subscription_until == club_date → ещё валиден.
        Q2 явно говорит "день-в-день, без grace period". Значит сегодня — последний
        день подписки, чек-ин разрешён, send_task ВЫЗЫВАЕТСЯ.
        """
        import asyncio

        async def _seed_today():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000091,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                sub_until = habit.club_date(datetime.now(tz=UTC))  # today
                m = Membership(
                    user_id=user.id, habit_id=habit.id,
                    status="active", subscription_until=sub_until,
                )
                s.add(m)
                await s.flush()
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed_today())

        with patch(
            "app.api.v1.internal_checkins.send_task", return_value="task-ok"
        ) as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "task-ok"
        mock_send.assert_called_once()

    def test_subscription_expired_priority_over_paused(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """Pravki-subscription-2026-08-17 §Z-22: combo status=paused + sub expired
        → SUBSCRIPTION_EXPIRED (НЕ MEMBERSHIP_PAUSED). Семантика: "продли подписку"
        лечит и подписку, и (через recompute пауз) возможный PAUSED.
        "Пополни депозит" лечит ТОЛЬКО PAUSED, а подписку не лечит → зацикливание.
        """
        import asyncio
        from datetime import timedelta

        async def _seed_combo():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000092,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                sub_until = habit.club_date(datetime.now(tz=UTC)) - timedelta(days=5)
                m = Membership(
                    user_id=user.id, habit_id=habit.id,
                    status="paused", subscription_until=sub_until,
                )
                s.add(m)
                await s.flush()
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed_combo())

        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        # ВАЖНО: subscription_expired, НЕ membership_paused.
        assert body["code"] == "subscription_expired"
        mock_send.assert_not_called()


class TestEnqueueCheckinForwarded:
    """Pravki §Z-22 (Step 4, hole #4): synchronous reject for forwarded messages."""

    def test_forwarded_returns_synchronous_reject(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """payload.is_forwarded=True → ok=False, code="forwarded",
        send_task НЕ вызван.
        """
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000009,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="active"
                )
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch("app.api.v1.internal_checkins.send_task") as mock_send:
            with TestClient(app) as client:
                payload = _payload_chat_id(chat_id)
                payload["is_forwarded"] = True
                r = client.post(
                    "/internal/checkins/process",
                    json=payload,
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is False
        assert body["code"] == "forwarded"
        mock_send.assert_not_called()

    def test_not_forwarded_enqueues_normally(
        self, app: Any, service_token: str, _sqlite_engine: Any
    ) -> None:
        """payload.is_forwarded=False (default) → ok=True, send_task ВЫЗВАЛСЯ."""
        import asyncio

        async def _seed():
            async with session_module._session_factory() as s:
                user = await _make_user(s, 7295309649)
                habit = await _make_habit(
                    s,
                    chat_id=-1000000000010,
                    checkin_window_start=dt_time(0, 0),
                    checkin_window_end=dt_time(23, 59, 59),
                )
                await _make_membership(
                    s, user_id=user.id, habit_id=habit.id, status="active"
                )
                await s.commit()
                return habit.chat_id

        chat_id = asyncio.run(_seed())

        with patch(
            "app.api.v1.internal_checkins.send_task", return_value="task-not-forwarded"
        ) as mock_send:
            with TestClient(app) as client:
                # is_forwarded НЕ передаётся — default False в pydantic
                r = client.post(
                    "/internal/checkins/process",
                    json=_payload_chat_id(chat_id),
                    headers={"X-Service-Token": service_token},
                )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "task-not-forwarded"
        mock_send.assert_called_once()

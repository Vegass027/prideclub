"""Тест для Z-2.8: catch-handler ловит IntegrityError на session.commit().

Сценарий: гонка двух параллельных catch'ей одного юзера на одну и ту же
(membership_id, date, reason). UNIQUE-индекс uq_penalty_per_day_reason
(миграция 002) срабатывает на INSERT второй транзакции — session.commit()
бросает IntegrityError. Без Z-2.8 это всплывает как 500, с Z-2.8 —
пользовательский CatchResponse(ok=False, code="penalty_already_processed").

Стратегия: вместо сложной симуляции гонки (которая в однопоточном тесте
ненадёжна), подменяем session.commit через dependency_overrides на функцию,
которая бросает IntegrityError. apply_catch при этом отрабатывает штатно
(мы его мокаем через прямую подмену метода).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
import urllib.parse
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.habit import Habit
from app.models.membership import Membership, MembershipStatus
from app.models.penalty import Penalty

# --- env bootstrap (аналогично test_topup.py) ------------------------------

os.environ.setdefault("STATIC_DIR", tempfile.mkdtemp(prefix="hc_catch_static_"))
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SSE_TOKEN_SECRET", "test-sse-token-secret")


# --- init_data хелпер (аналогично test_topup.py) ---------------------------


def _build_init_data(*, user_id: int, bot_token: str = "test-bot-token") -> str:
    user = {"id": user_id, "first_name": "Catcher", "username": "catcher"}
    params = {
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "query_id": "q",
    }
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(params)


# --- test ------------------------------------------------------------------


def _build_fake_habit() -> Habit:
    """Минимальный Habit, у которого работает .club_date()."""
    habit_id = str(uuid4())
    return Habit(
        id=habit_id,
        title="T",
        chat_id=-100,
        checkin_window_start=datetime.now().time(),
        checkin_window_end=datetime.now().time(),
        timezone="Europe/Moscow",
        penalty_amount=1000,
        price_month=100_00,
        proof_type="video_note",
        proof_types=["video_note"],
        is_active=True,
    )


def _build_fake_membership(user_id: int = 999, habit_id: str = "any-habit-id") -> Membership:
    return Membership(
        id=str(uuid4()),
        user_id=user_id,
        habit_id=habit_id,
        status=MembershipStatus.ACTIVE,
    )


def _build_fake_penalty() -> Penalty:
    return Penalty(
        id=str(uuid4()),
        membership_id=str(uuid4()),
        catcher_membership_id=str(uuid4()),
        amount=1000,
        fund_share=1000,
        catcher_bonus_points=1,
        reason="caught",
        date=datetime.now(tz=UTC).date(),
        bonus_applied=False,
    )


@pytest.mark.asyncio
async def test_catch_handler_integrity_error_returns_already_processed() -> None:
    """Z-2.8: IntegrityError на commit → ok=False, code='penalty_already_processed'."""
    from app.db import session as session_module
    from app.main import create_app

    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_catch_test_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine  # noqa: SLF001
    session_module._session_factory = factory  # noqa: SLF001

    app = create_app()

    # Прокси-сессия: commit() бросает IntegrityError, всё остальное — pass-through.
    class _IntegrityOnCommitSession:
        def __init__(self) -> None:
            self.commit_called = False

        async def commit(self) -> None:
            self.commit_called = True
            raise IntegrityError(
                "INSERT INTO penalties",
                params={},
                orig=Exception("duplicate key value violates unique constraint"),
            )

        async def rollback(self) -> None:
            pass

        async def execute(self, stmt, *args, **kwargs):
            return None

        async def flush(self) -> None:
            pass

        def add(self, obj, *args, **kwargs):
            pass

    wrapper_session = _IntegrityOnCommitSession()

    async def _override_get_session():
        yield wrapper_session

    from app.db.session import get_session

    app.dependency_overrides[get_session] = _override_get_session

    fake_habit = _build_fake_habit()
    fake_membership = _build_fake_membership()
    fake_penalty = _build_fake_penalty()

    # Мокаем все репо-методы, которые вызывает роут, чтобы не упирались в wrapper_session.
    # Также override'им current_user_db — она делает UserRepository.upsert через Postgres
    # ON CONFLICT, что не работает с AsyncMock-сессией. Нам в этом тесте достаточно того,
    # что telegram_user уже провалидирован AuthMiddleware.
    async def _fake_current_user_db():
        from app.core.security import TelegramUser
        return TelegramUser(
            id=999,
            first_name="Catcher",
            last_name=None,
            username="catcher",
            language_code=None,
            is_premium=False,
            auth_date=int(time.time()),
        )

    from app.api.v1.users import current_user_db

    app.dependency_overrides[current_user_db] = _fake_current_user_db

    with patch("app.api.v1.members.HabitRepository") as HabitRepoMock, \
         patch("app.api.v1.members.MembershipRepository") as MemberRepoMock, \
         patch("app.api.v1.members.CheckinRepository") as CheckinRepoMock, \
         patch("app.api.v1.members.SuspiciousPairsRepository") as SuspRepoMock, \
         patch(
             "app.api.v1.members.PenaltyService.apply_catch",
             new=AsyncMock(return_value=fake_penalty),
         ):
        HabitRepoMock.return_value.get = AsyncMock(return_value=fake_habit)
        MemberRepoMock.return_value.get_for_user_in_habit = AsyncMock(return_value=fake_membership)
        # Pravki-bug-fixes §Z-21 (Item 6): catch_violator теперь читает
        # violator_membership через membership_repo.get(payload.violator_membership_id)
        # для broadcast payload. Возвращаем тот же fake_membership.
        MemberRepoMock.return_value.get = AsyncMock(return_value=fake_membership)
        CheckinRepoMock.return_value.get_for_date = AsyncMock(return_value=None)
        SuspRepoMock.return_value.lookup_flagged = AsyncMock(return_value=False)

        with TestClient(app) as client:
            r = client.post(
                "/api/v1/habits/any-habit-id/catch",
                headers={"X-Telegram-Init-Data": _build_init_data(user_id=999)},
                json={"violator_membership_id": str(uuid4())},
            )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False, body
    assert body["code"] == "penalty_already_processed", body
    assert wrapper_session.commit_called, "commit() должен был быть вызван"

    await engine.dispose()
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_catch_handler_normal_path_does_not_catch_integrity_error() -> None:
    """Контр-тест: при штатном commit (без IntegrityError) handler НЕ
    конвертирует ответ в penalty_already_processed.

    Подменяем apply_catch на мок, который бросает PenaltyAlreadyProcessedError
    напрямую — handler должен пробросить его в существующий except-блок.
    """
    from app.core.exceptions import PenaltyAlreadyProcessedError
    from app.db import session as session_module
    from app.main import create_app

    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_catch_test_ok_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine  # noqa: SLF001
    session_module._session_factory = factory  # noqa: SLF001

    app = create_app()

    wrapper_session = AsyncMock()

    async def _override_get_session():
        yield wrapper_session

    from app.db.session import get_session

    app.dependency_overrides[get_session] = _override_get_session

    fake_habit = _build_fake_habit()
    fake_membership = _build_fake_membership()

    async def _fake_current_user_db():
        from app.core.security import TelegramUser
        return TelegramUser(
            id=999,
            first_name="Catcher",
            last_name=None,
            username="catcher",
            language_code=None,
            is_premium=False,
            auth_date=int(time.time()),
        )

    from app.api.v1.users import current_user_db

    app.dependency_overrides[current_user_db] = _fake_current_user_db

    with patch("app.api.v1.members.HabitRepository") as HabitRepoMock, \
         patch("app.api.v1.members.MembershipRepository") as MemberRepoMock, \
         patch("app.api.v1.members.CheckinRepository") as CheckinRepoMock, \
         patch("app.api.v1.members.SuspiciousPairsRepository") as SuspRepoMock, \
         patch(
             "app.api.v1.members.PenaltyService.apply_catch",
             new=AsyncMock(
                 side_effect=PenaltyAlreadyProcessedError(
                     "deposit_exhausted", code="deposit_exhausted"
                 )
             ),
         ):
        HabitRepoMock.return_value.get = AsyncMock(return_value=fake_habit)
        MemberRepoMock.return_value.get_for_user_in_habit = AsyncMock(return_value=fake_membership)
        # Pravki-bug-fixes §Z-21 (Item 6): catch_violator теперь читает
        # violator_membership через membership_repo.get(payload.violator_membership_id)
        # для broadcast payload. Возвращаем тот же fake_membership.
        MemberRepoMock.return_value.get = AsyncMock(return_value=fake_membership)
        CheckinRepoMock.return_value.get_for_date = AsyncMock(return_value=None)
        SuspRepoMock.return_value.lookup_flagged = AsyncMock(return_value=False)

        with TestClient(app) as client:
            r = client.post(
                "/api/v1/habits/any-habit-id/catch",
                headers={"X-Telegram-Init-Data": _build_init_data(user_id=999)},
                json={"violator_membership_id": str(uuid4())},
            )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["code"] == "deposit_exhausted"

    await engine.dispose()
    app.dependency_overrides.clear()


# ============================================================================
# Pravki-bug-fixes §Z-21 (Item 6): catch_violator → publish_catch_event
#
# Цель: проверить что после успешного commit() handler отправляет Celery-таску
# publish_catch_event с правильным payload И что исключение из send_task
# (broker down, redis down, что угодно) НЕ ломает HTTP-ответ.
#
# Паттерн: TestClient + патчи репозиториев + патч celery_producer.send_task.
# ============================================================================


@pytest.mark.asyncio
async def test_catch_handler_sends_publish_catch_event_after_commit() -> None:
    """После успешного commit() handler вызывает send_task("publish_catch_event", {...})
    с правильным payload (habit_id, penalty_id, user_id'ы, amount)."""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from app.api.v1.users import current_user_db
    from app.db import session as session_module
    from app.main import create_app

    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_catch_publish_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine
    session_module._session_factory = factory

    fake_habit_id = str(uuid4())
    fake_membership_id = str(uuid4())
    fake_penalty_id = str(uuid4())
    fake_violator_user_id = 8888

    fake_habit = MagicMock()
    fake_habit.id = fake_habit_id
    fake_habit.chat_id = -1004348250990
    fake_habit.club_date = MagicMock(return_value=datetime.now(UTC).date())

    fake_catcher_membership = MagicMock()
    fake_catcher_membership.id = str(uuid4())

    fake_penalty = MagicMock()
    fake_penalty.id = fake_penalty_id
    fake_penalty.amount = 10000

    fake_violator_membership = MagicMock()
    fake_violator_membership.id = fake_membership_id
    fake_violator_membership.user_id = fake_violator_user_id

    captured_calls = []

    def fake_send_task(task_kind: str, payload: dict) -> str:
        captured_calls.append((task_kind, payload))
        return "task-id-stub"

    with patch("app.api.v1.members.HabitRepository") as HabitRepoMock, \
         patch("app.api.v1.members.MembershipRepository") as MemberRepoMock, \
         patch("app.api.v1.members.CheckinRepository") as CheckinRepoMock, \
         patch("app.api.v1.members.SuspiciousPairsRepository") as SuspRepoMock, \
         patch(
             "app.api.v1.members.PenaltyService.apply_catch",
             new=AsyncMock(return_value=fake_penalty),
         ), \
         patch(
             "app.services.celery_producer.send_task",
             side_effect=fake_send_task,
         ):

        HabitRepoMock.return_value.get = AsyncMock(return_value=fake_habit)
        MemberRepoMock.return_value.get_for_user_in_habit = AsyncMock(return_value=fake_catcher_membership)
        MemberRepoMock.return_value.get = AsyncMock(return_value=fake_violator_membership)
        CheckinRepoMock.return_value.get_for_date = AsyncMock(return_value=None)
        SuspRepoMock.return_value.lookup_flagged = AsyncMock(return_value=False)

        app = create_app()

        # Fake current_user_db — возвращает telegram_user с id=999 (catcher)
        from app.core.security import TelegramUser
        async def _fake_user_db(session=None):
            return TelegramUser(
                id=999, first_name="Catcher", last_name=None, username=None,
                language_code=None, is_premium=False,
                auth_date=int(time.time()),
            )

        app.dependency_overrides[current_user_db] = _fake_user_db

        try:
            with TestClient(app) as client:
                r = client.post(
                    f"/api/v1/habits/{fake_habit_id}/catch",
                    headers={"X-Telegram-Init-Data": _build_init_data(user_id=999)},
                    json={"violator_membership_id": fake_membership_id},
                )

            # ГЛАВНОЕ: HTTP-ответ — успех (Penalty в БД).
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True, body
            assert body["amount"] == 10000, body

            # ГЛАВНОЕ: send_task вызван с правильным payload.
            assert len(captured_calls) == 1, (
                f"send_task должен быть вызван 1 раз, было: {len(captured_calls)}"
            )
            task_kind, payload = captured_calls[0]
            assert task_kind == "publish_catch_event", task_kind
            assert payload["habit_id"] == fake_habit_id, payload
            assert payload["penalty_id"] == fake_penalty_id, payload
            assert payload["catcher_user_id"] == 999, payload
            assert payload["violator_user_id"] == fake_violator_user_id, payload
            assert payload["violator_membership_id"] == fake_membership_id, payload
            assert payload["amount"] == 10000, payload
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()
            session_module._engine = None
            session_module._session_factory = None
            try:
                os.unlink(db_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_catch_handler_send_task_failure_does_not_break_response() -> None:
    """Если send_task (broker down / redis down) бросает исключение, handler
    НЕ пробрасывает его наружу — Penalty в БД, HTTP-ответ ok=True.

    Broadcast failure is at-most-once (UI-hint), не должен ломать HTTP response."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from app.api.v1.users import current_user_db
    from app.core.security import TelegramUser
    from app.db import session as session_module
    from app.main import create_app

    get_settings.cache_clear()

    tmp_dir = tempfile.mkdtemp(prefix="hc_catch_publish_fail_")
    db_path = os.path.join(tmp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine
    session_module._session_factory = factory

    fake_habit_id = str(uuid4())
    fake_membership_id = str(uuid4())
    fake_penalty_id = str(uuid4())

    fake_habit = MagicMock()
    fake_habit.id = fake_habit_id
    fake_habit.chat_id = -1004348250990
    fake_habit.club_date = MagicMock(return_value=datetime.now(UTC).date())

    fake_catcher_membership = MagicMock()
    fake_catcher_membership.id = str(uuid4())

    fake_penalty = MagicMock()
    fake_penalty.id = fake_penalty_id
    fake_penalty.amount = 10000

    fake_violator_membership = MagicMock()
    fake_violator_membership.id = fake_membership_id
    fake_violator_membership.user_id = 8888

    def fake_send_task_broken(task_kind, payload):
        # Симулируем kombu.exceptions.OperationalError (broker down).
        from kombu.exceptions import OperationalError

        raise OperationalError("broker unreachable")

    with patch("app.api.v1.members.HabitRepository") as HabitRepoMock, \
         patch("app.api.v1.members.MembershipRepository") as MemberRepoMock, \
         patch("app.api.v1.members.CheckinRepository") as CheckinRepoMock, \
         patch("app.api.v1.members.SuspiciousPairsRepository") as SuspRepoMock, \
         patch(
             "app.api.v1.members.PenaltyService.apply_catch",
             new=AsyncMock(return_value=fake_penalty),
         ), \
         patch(
             "app.services.celery_producer.send_task",
             side_effect=fake_send_task_broken,
         ):

        HabitRepoMock.return_value.get = AsyncMock(return_value=fake_habit)
        MemberRepoMock.return_value.get_for_user_in_habit = AsyncMock(return_value=fake_catcher_membership)
        MemberRepoMock.return_value.get = AsyncMock(return_value=fake_violator_membership)
        CheckinRepoMock.return_value.get_for_date = AsyncMock(return_value=None)
        SuspRepoMock.return_value.lookup_flagged = AsyncMock(return_value=False)

        app = create_app()

        async def _fake_user_db(session=None):
            return TelegramUser(
                id=999, first_name="Catcher", last_name=None, username=None,
                language_code=None, is_premium=False,
                auth_date=int(time.time()),
            )

        app.dependency_overrides[current_user_db] = _fake_user_db

        try:
            with TestClient(app) as client:
                r = client.post(
                    f"/api/v1/habits/{fake_habit_id}/catch",
                    headers={"X-Telegram-Init-Data": _build_init_data(user_id=999)},
                    json={"violator_membership_id": fake_membership_id},
                )

            # ГЛАВНОЕ: HTTP-ответ всё равно успех (penalty уже в БД).
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True, body
            assert body["amount"] == 10000, body
            # broadcast failure — at-most-once, не критично для HTTP response.
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()
            session_module._engine = None
            session_module._session_factory = None
            try:
                os.unlink(db_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

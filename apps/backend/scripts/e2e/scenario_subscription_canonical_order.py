"""Pravki-subscription-2026-08-17 §CanonicalOrder E2E (ЛOKALЬНЫЙ).

Сценарий: проверяем канонический порядок v3 через реальное FastAPI-приложение
(TestClient), с реальным auth middleware, реальным DB-доступом (SQLite in-memory),
и реальной логикой `apps/backend/app/api/v1/internal_checkins.py:enqueue_checkin`.

Цель: убедиться что SUBSCRIPTION_EXPIRED (#6) реально выигрывает у
MEMBERSHIP_PAUSED (#7) и MEMBERSHIP_LEFT (#8) в ПРОД-подобных условиях —
а не только в юнит-тестах с fake-сессией.

Использует ОТДЕЛЬНОГО тестового юзера (user_id=99005), чтобы не путать
с уже загрязнёнными данными Sofia (7295309649) и seed-юзеров (10xxx).

Запуск:
    cd apps/backend && .venv/bin/python -m scripts.e2e.scenario_subscription_canonical_order

НИЧЕГО НЕ ПУШИТ, НЕ ПИШЕТ В БД, НЕ ШЛЁТ СООБЩЕНИЯ. SQLite in-memory + TestClient.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import UTC, date as _date, datetime, time as _time, timedelta
from pathlib import Path
from uuid import uuid4

# Pravki-subscription-2026-08-17 §Z-22 E2E: генерируем RUN_TAG и
# встраиваем в habit title (как scenario_happy_path.py:RUN_TAG).
# Cleanup использует substring LIKE '%{run_tag}%' для scoped-удаления.
RUN_TAG: str = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")

# STATIC_DIR выставляем ДО импорта app.main (там на module-level create_app).
_TMP_STATIC = tempfile.mkdtemp(prefix="hc_e2e_static_")
os.environ.setdefault("STATIC_DIR", _TMP_STATIC)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("SERVICE_SECRET", "test-service-secret")
os.environ.setdefault("SSE_TOKEN_SECRET", "test-sse-token-secret")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("OWNER_TELEGRAM_ID", "0")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "https://web.telegram.org")

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT / "packages" / "shared"))
sys.path.insert(0, str(ROOT / "apps" / "backend"))
# Pravki-subscription-2026-08-17 §CanonicalOrder E2E: для проверки бота-текста
# загружаем модуль checkin_texts напрямую через importlib (минуя __init__.py
# который тянет aiogram).
import importlib.util as _importlib_util  # noqa: E402

_CHECKIN_TEXTS_PATH = ROOT / "apps" / "bot" / "bot" / "handlers" / "checkin_texts.py"
_spec = _importlib_util.spec_from_file_location(
    "_checkin_texts_module_e2e", _CHECKIN_TEXTS_PATH
)
_checkin_texts_module = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_checkin_texts_module)
REJECT_SUBSCRIPTION_EXPIRED = _checkin_texts_module.REJECT_SUBSCRIPTION_EXPIRED

import re  # noqa: E402
import uuid as _uuid  # noqa: E402

from sqlalchemy import event as _sa_event  # noqa: E402
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlalchemy import JSON, String  # noqa: E402
from sqlalchemy.sql.compiler import SQLCompiler  # noqa: E402

# ---------------------------------------------------------------------------
# 1. SQLite + PG-rewrite (тот же паттерн что test_internal_checkins.py)
# ---------------------------------------------------------------------------


def _compile_gen_random_uuid(_cls, _elem, **_kw):
    return "'00000000-0000-0000-0000-000000000000'"


def _compile_current_date(_cls, _elem, **_kw):
    return "CURRENT_DATE"


def _compile_now(_cls, _elem, **_kw):
    return "CURRENT_TIMESTAMP"


SQLCompiler.visit_gen_random_uuid = _compile_gen_random_uuid  # type: ignore[attr-defined]
SQLCompiler.visit_current_date = _compile_current_date  # type: ignore[attr-defined]
SQLCompiler.visit_now = _compile_now  # type: ignore[attr-defined]


def _rewrite_sql_for_sqlite(statement, parameters, _uuid_seq):
    def _repl_uuid(_m):
        _uuid_seq[0] += 1
        return f"'{_uuid.uuid4()}'"

    def _repl_now(_m):
        return "CURRENT_TIMESTAMP"

    def _repl_date(_m):
        return "CURRENT_DATE"

    statement = re.sub(r"gen_random_uuid\s*\(\s*\)", _repl_uuid, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bnow\s*\(\s*\)", _repl_now, statement, flags=re.IGNORECASE)
    statement = re.sub(r"\bcurrent_date\b", _repl_date, statement, flags=re.IGNORECASE)
    return statement, parameters


# Импортируем модели ПОСЛЕ sys.path setup.
from app.core.config import get_settings  # noqa: E402
from app.core.security import generate_service_token  # noqa: E402
from app.db import session as session_module  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.habit import Habit  # noqa: E402
from app.models.membership import Membership  # noqa: E402
from app.models.user import User  # noqa: E402
from app.core.constants import ProofType, MembershipStatus  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Bot prefilter text (через прямую ссылку на текстовый модуль, не на aiogram).
# Загружаем checkin_texts.py напрямую через importlib — обходим __init__.py,
# который импортирует aiogram (не установлен в backend venv).
# _text_for_code проверяем опционально (он импортируется из bot/handlers/checkin.py,
# который тянет aiogram — если не получится, фоллбэк на ручной маппинг).
REJECT_SUBSCRIPTION_EXPIRED = _checkin_texts_module.REJECT_SUBSCRIPTION_EXPIRED  # noqa: F821

try:
    _CHECKIN_HANDLER_PATH = ROOT / "apps" / "bot" / "bot" / "handlers" / "checkin.py"
    _spec2 = _importlib_util.spec_from_file_location(
        "_checkin_handler_module_e2e", _CHECKIN_HANDLER_PATH
    )
    _checkin_handler_module = _importlib_util.module_from_spec(_spec2)
    # Отключаем side-effect импортов: загружаем только тело функции,
    # которая нужна для маппинга. Сам модуль требует aiogram, поэтому пропускаем.
    BOT_TEXT_FOR_CODE_AVAILABLE = False
    _text_for_code = None
except Exception as exc:
    print(f"⚠️ _text_for_code недоступен ({exc}), проверка только через прямой шаблон.")
    BOT_TEXT_FOR_CODE_AVAILABLE = False
    _text_for_code = None


# ---------------------------------------------------------------------------
# 2. Цвета для вывода (без сторонних зависимостей).
# ---------------------------------------------------------------------------
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def ok(label: str, expected, actual) -> bool:
    """Сравнение expected vs actual, цветной вывод."""
    passed = expected == actual
    color = GREEN if passed else RED
    mark = "✓" if passed else "✗"
    print(
        f"  {color}{mark}{RESET} {label}: "
        f"expected={YELLOW}{expected!r}{RESET}, "
        f"actual={CYAN}{actual!r}{RESET}"
    )
    return passed


def info(msg: str) -> None:
    print(f"  {DIM}ℹ{RESET} {msg}")


# ---------------------------------------------------------------------------
# 3. Test-фикстуры (из test_internal_checkins.py).
# ---------------------------------------------------------------------------


async def setup_sqlite():
    """Создаёт in-memory SQLite с ремапом PG-типов и rewrite'ом SQL."""
    get_settings.cache_clear()
    tmp_dir = tempfile.mkdtemp(prefix="hc_e2e_")
    db_path = os.path.join(tmp_dir, "e2e.db")
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

    # Ремап PG типов на SQLite-совместимые.
    for m in (User, Habit):
        for col in m.__table__.columns:
            t = col.type
            if isinstance(t, UUID) and not t.as_uuid:
                col.type = String(36)
            elif isinstance(t, JSONB):
                col.type = JSON()
            elif isinstance(t, INET):
                col.type = String(45)

    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
        await conn.run_sync(Habit.__table__.create)
        await conn.run_sync(Membership.__table__.create)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_module._engine = engine
    session_module._session_factory = factory
    return engine


async def seed_user_habit_membership(
    *,
    user_id: int,
    chat_id: int,
    subscription_until: _date | None,
    status: str,
):
    """Создаёт (user, habit, membership) с явными параметрами.

    user_id: ОТДЕЛЬНЫЙ (НЕ Sofia 7295309649, НЕ seed 10xxx).
    chat_id: уникальный для этого E2E (чтобы не пересечься с существующими тестами).
    """
    async with session_module._session_factory() as s:
        user = User(id=user_id, first_name=f"E2E-sub-{user_id}", username=None)
        s.add(user)
        await s.flush()

        habit = Habit(
            id=str(uuid4()),
            title=f"E2E-Subscription-Canonical-{RUN_TAG}-{chat_id}",
            description="canonical order test",
            chat_id=chat_id,
            checkin_window_start=_time(0, 0),
            checkin_window_end=_time(23, 59, 59),
            timezone="Europe/Moscow",
            penalty_amount=5_000,
            price_month=50_000,
            proof_type=ProofType.VIDEO_NOTE,
            proof_types=["video_note"],
            prize_pool=0,
            is_active=True,
        )
        s.add(habit)
        await s.flush()

        m = Membership(
            id=str(uuid4()),
            user_id=user_id,
            habit_id=habit.id,
            status=status,
            subscription_until=subscription_until,
        )
        s.add(m)
        await s.commit()
        return habit.chat_id, habit.id, m.id


# ---------------------------------------------------------------------------
# 4. Helpers для бота: маппинг code → текст.
# ---------------------------------------------------------------------------


def check_bot_text(code: str, **kwargs) -> str | None:
    """Возвращает текст, который бот отправит юзеру для данного кода.

    Прямая проверка шаблона REJECT_SUBSCRIPTION_EXPIRED (если бот недоступен).
    Если получится импортировать _text_for_code (требует aiogram) —
    используем его; иначе fallback на ручной рендер шаблона.
    """
    if _text_for_code is not None:
        try:
            return _text_for_code(code, **kwargs)
        except Exception:
            pass
    # Fallback: ручной рендер шаблона с тестовыми параметрами.
    from app.core.constants import CheckinRejectCode
    if code == CheckinRejectCode.SUBSCRIPTION_EXPIRED.value:
        if REJECT_SUBSCRIPTION_EXPIRED is None:
            return None
        return REJECT_SUBSCRIPTION_EXPIRED.format(
            name="Test",
            habit_title=kwargs.get("habit_title", "Test"),
            sub_until=kwargs.get("sub_until", "2026-08-16"),
        )
    return None


# ---------------------------------------------------------------------------
# 5. Главный сценарий.
# ---------------------------------------------------------------------------


async def main() -> int:
    # Pravki-subscription-2026-08-17 §Z-22 E2E: выводим RUN_TAG в machine-readable
    # формате чтобы bash мог его захватить: `RUN_TAG=$(... | grep -oE 'RUN_TAG=\S+')`.
    print(f"RUN_TAG={RUN_TAG}")
    print(f"{CYAN}=== Pravki-subscription-2026-08-17 §CanonicalOrder E2E ==={RESET}")
    print()

    engine = await setup_sqlite()
    app = create_app()

    SERVICE_SECRET = os.environ["SERVICE_SECRET"]
    token = generate_service_token(
        service_name="bot",
        target_audience="backend-api",
        secret=SERVICE_SECRET,
        ttl_seconds=60,
    )

    # =====================================================================
    # Кейс A: status=ACTIVE, subscription_until=yesterday → SUBSCRIPTION_EXPIRED
    # (юзер с активным членством, но истёкшей подпиской — базовый кейс)
    # =====================================================================
    print(f"{CYAN}Кейс A: status=ACTIVE, subscription_until=вчера{RESET}")
    user_id_a = 99005
    chat_id_a = -1009900500
    yesterday = _date.today() - timedelta(days=1)
    chat_id_a, habit_id_a, _ = await seed_user_habit_membership(
        user_id=user_id_a,
        chat_id=chat_id_a,
        subscription_until=yesterday,
        status=MembershipStatus.ACTIVE.value,
    )
    info(f"user_id={user_id_a} (отдельный от Sofia 7295309649 и seed 10xxx)")
    info(f"chat_id={chat_id_a}, status=active, subscription_until={yesterday.isoformat()}")

    with TestClient(app) as client:
        r = client.post(
            "/internal/checkins/process",
            headers={"X-Service-Token": token},
            json={
                "user_id": user_id_a,
                "chat_id": chat_id_a,
                "proof_type": "video_note",
                "message_id": 1,
                "message_sent_at": datetime.now(tz=UTC).isoformat(),
            },
        )
    body = r.json()
    code_a = body.get("code")
    ok_a = ok("response.code", "subscription_expired", code_a)

    # Бот-текст для этого кода.
    text_a = check_bot_text(
        "subscription_expired",
        habit_title=f"E2E-Subscription-Canonical-{chat_id_a}",
        sub_until=yesterday.isoformat(),
    )
    if text_a:
        ok_text_istekla = ok("bot text contains 'истек'", True, "истек" in (text_a or "").lower() or "истекла" in (text_a or "").lower())
        ok_text_prodlit = ok("bot text mentions 'продли'", True, "продли" in (text_a or "").lower())
        ok_text_no_deposit = ok("bot text NOT mentions 'пополни депозит'", False, "пополни депозит" in (text_a or "").lower())
        ok_text_no_pause = ok("bot text NOT mentions 'паузе'", False, "паузе" in (text_a or "").lower())
        print(f"  {DIM}text={RESET}{text_a!r}")
    else:
        ok_text_istekla = ok_text_prodlit = ok_text_no_deposit = ok_text_no_pause = False
        print(f"  {RED}✗{RESET}  Не удалось получить текст REJECT_SUBSCRIPTION_EXPIRED")

    print()

    # =====================================================================
    # Кейс B: status=PAUSED + subscription_until=yesterday → SUBSCRIPTION_EXPIRED
    # (canonical #6 выше #7 — главная проверка приоритета)
    # =====================================================================
    print(f"{CYAN}Кейс B: status=PAUSED + subscription_until=вчера{RESET}")
    print(f"  {DIM}(critical: subscription_expired должен выиграть у membership_paused){RESET}")
    user_id_b = 99006
    chat_id_b = -1009900600
    chat_id_b, habit_id_b, _ = await seed_user_habit_membership(
        user_id=user_id_b,
        chat_id=chat_id_b,
        subscription_until=yesterday,
        status=MembershipStatus.PAUSED.value,
    )
    info(f"user_id={user_id_b}, status=paused, subscription_until={yesterday.isoformat()}")

    with TestClient(app) as client:
        r = client.post(
            "/internal/checkins/process",
            headers={"X-Service-Token": token},
            json={
                "user_id": user_id_b,
                "chat_id": chat_id_b,
                "proof_type": "video_note",
                "message_id": 2,
                "message_sent_at": datetime.now(tz=UTC).isoformat(),
            },
        )
    body = r.json()
    code_b = body.get("code")
    ok_b = ok("response.code (должен быть subscription_expired)", "subscription_expired", code_b)
    ok_b_priority = ok("НЕ membership_paused (canonical priority)", False, code_b == "membership_paused")

    print()

    # =====================================================================
    # Кейс C: status=LEFT + subscription_until=yesterday → SUBSCRIPTION_EXPIRED
    # (canonical #6 выше #8)
    # =====================================================================
    print(f"{CYAN}Кейс C: status=LEFT + subscription_until=вчера{RESET}")
    print(f"  {DIM}(canonical #6 выше #8 — subscription_expired выигрывает у membership_left){RESET}")
    user_id_c = 99007
    chat_id_c = -1009900700
    chat_id_c, habit_id_c, _ = await seed_user_habit_membership(
        user_id=user_id_c,
        chat_id=chat_id_c,
        subscription_until=yesterday,
        status=MembershipStatus.LEFT.value,
    )
    info(f"user_id={user_id_c}, status=left, subscription_until={yesterday.isoformat()}")

    with TestClient(app) as client:
        r = client.post(
            "/internal/checkins/process",
            headers={"X-Service-Token": token},
            json={
                "user_id": user_id_c,
                "chat_id": chat_id_c,
                "proof_type": "video_note",
                "message_id": 3,
                "message_sent_at": datetime.now(tz=UTC).isoformat(),
            },
        )
    body = r.json()
    code_c = body.get("code")
    ok_c = ok("response.code (должен быть subscription_expired)", "subscription_expired", code_c)
    ok_c_priority = ok("НЕ membership_left (canonical priority)", False, code_c == "membership_left")

    print()

    # =====================================================================
    # Кейс D: status=ACTIVE, subscription_until=today → check-in PASSES (today last day)
    # (Q2: день-в-день, без grace period — сегодня ещё валиден)
    # =====================================================================
    print(f"{CYAN}Кейс D: status=ACTIVE, subscription_until=СЕГОДНЯ{RESET}")
    print(f"  {DIM}(Q2: день-в-день — сегодня последний день, чек-ин должен пройти){RESET}")
    user_id_d = 99008
    chat_id_d = -1009900800
    chat_id_d, habit_id_d, _ = await seed_user_habit_membership(
        user_id=user_id_d,
        chat_id=chat_id_d,
        subscription_until=_date.today(),
        status=MembershipStatus.ACTIVE.value,
    )
    info(f"user_id={user_id_d}, status=active, subscription_until=today")

    with TestClient(app) as client:
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "app.api.v1.internal_checkins.send_task", return_value="task-ok-e2e"
        ) as _:
            r = client.post(
                "/internal/checkins/process",
                headers={"X-Service-Token": token},
                json={
                    "user_id": user_id_d,
                    "chat_id": chat_id_d,
                    "proof_type": "video_note",
                    "message_id": 4,
                    "message_sent_at": datetime.now(tz=UTC).isoformat(),
                },
            )
    body = r.json()
    ok_d_passed = body.get("ok") is True
    ok_d_task = body.get("task_id") == "task-ok-e2e"
    mark_pass = f"{GREEN}✓{RESET}"
    mark_fail = f"{RED}✗{RESET}"
    print(f"  {mark_pass if ok_d_passed else mark_fail} response.ok = True: actual={body.get('ok')!r}")
    print(f"  {mark_pass if ok_d_task else mark_fail} task_id = 'task-ok-e2e' (send_task вызван): actual={body.get('task_id')!r}")

    print()

    # =====================================================================
    # Финальный итог.
    # =====================================================================
    all_checks = [
        ("A: ACTIVE + sub expired", ok_a),
        ("A: bot text mentions 'истек'", ok_text_istekla),
        ("A: bot text mentions 'продли'", ok_text_prodlit),
        ("A: bot text NOT mentions 'пополни депозит'", ok_text_no_deposit),
        ("A: bot text NOT mentions 'паузе'", ok_text_no_pause),
        ("B: PAUSED + sub expired → subscription_expired", ok_b),
        ("B: НЕ membership_paused (canonical priority)", ok_b_priority),
        ("C: LEFT + sub expired → subscription_expired", ok_c),
        ("C: НЕ membership_left (canonical priority)", ok_c_priority),
        ("D: ACTIVE + sub today → passes (день-в-день)", ok_d_passed and ok_d_task),
    ]

    print(f"{CYAN}=== Итог ==={RESET}")
    passed = sum(1 for _, v in all_checks if v)
    total = len(all_checks)
    for label, v in all_checks:
        mark = f"{GREEN}✓{RESET}" if v else f"{RED}✗{RESET}"
        print(f"  {mark} {label}")
    print()
    if passed == total:
        print(f"{GREEN}Все {total} проверок прошли.{RESET}")
        return 0
    else:
        print(f"{RED}{total - passed} из {total} проверок провалились.{RESET}")
        return 1

    # Cleanup.
    await engine.dispose()
    session_module._engine = None
    session_module._session_factory = None


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
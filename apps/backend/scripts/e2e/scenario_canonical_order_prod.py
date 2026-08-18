"""Real E2E for canonical order v3: реальный HTTP + prod Postgres.

Pravki-subscription-2026-08-17 §Z-22 (real E2E).

Использует E2EHttp → реальный HTTPS → api.prideclub.fun → /internal/checkins/process.
Реальный PostgreSQL через E2EDatabase для setup (нет API для users и
для прямого UPDATE membership.subscription_until / status).

Отличия от scenario_subscription_canonical_order.py:
- Тот использует FastAPI TestClient + in-memory SQLite (tautology).
- Этот сценарий создаёт реальные данные в prod Postgres и гоняет через
  настоящий nginx → backend → реальный Postgres. Ловит расхождения
  SQLite vs PostgreSQL (типы UUID/DATE, индексы, транзакции),
  и реальный network-path (timeout, CORS, rate-limit middleware).

Запуск:
    docker exec habit-backend python -m scripts.e2e.scenario_canonical_order_prod

После прогона:
    # захватить RUN_TAG из stdout первой строкой
    # cleanup с тем же RUN_TAG и --user-ids 99005,99006,99007,99008
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from scripts.e2e.auth import FakeUser
from scripts.e2e.core import E2EDatabase, E2EHttp, Secrets, load_secrets


# Synthetic user_ids — НЕ Sofia (7295309649), НЕ seed (10xxx), НЕ happy_path (99001-99003).
USER_IDS: list[int] = [99005, 99006, 99007, 99008]

# Habit's synthetic chat_id (не существующая Telegram группа — бот попытается
# отправить ответ и получит ChatNotFound, но prefilter+gate работают ДО этого).
# Используем short_id в диапазоне 9 цифр чтобы не вылезать за лимит Telegram.
import time as _time

_SHORT_CHAT_ID: int = int(_time.time()) % 10_000_000_000
_CHAT_ID: int = -100_000_000_0000 - _SHORT_CHAT_ID

RUN_TAG: str = datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass
class CaseResult:
    name: str
    expected_code: str
    actual_code: str | None
    http_status: int
    passed: bool


def _ok(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def _err(s: str) -> str:
    return f"\033[31m{s}\033[0m"


def _check(result: CaseResult) -> None:
    mark = "✓" if result.passed else "✗"
    color = _ok if result.passed else _err
    print(
        f"  {color(mark)} {result.name}: "
        f"HTTP {result.http_status}, code={result.actual_code!r} "
        f"(expected {result.expected_code!r})"
    )


async def setup_users(db: E2EDatabase) -> None:
    """Создать synthetic users 99005-99008 через прямой INSERT.

    Нет API для создания users — только прямой SQL.
    deposit_balance=200₽ достаточно для штрафа (penalty=100₽) и
    проверки recompute_pause_status при смене баланса.

    Минимальный INSERT — только NOT NULL колонки без server_default:
    id (PK), first_name. timezone имеет default, остальные nullable.
    """
    print("=== Setup: create synthetic users 99005-99008 ===")
    async with db.session() as conn:
        for uid in USER_IDS:
            # ON CONFLICT DO NOTHING — если предыдущий прогон не почистился,
            # повторный прогон не падает.
            await conn.execute(
                "INSERT INTO users "
                "(id, first_name, username, deposit_balance, timezone) "
                "VALUES ($1, $2, $3, $4, $5) "
                "ON CONFLICT (id) DO NOTHING",
                uid,
                f"E2E-{uid}",
                f"e2e_{uid}",
                200_00,  # 200₽ — выше penalty (100₽), чтобы не flip'нуть в PAUSED
                "Europe/Moscow",
            )
        # Подтверждаем что 4 users созданы (или уже были).
        rows = await conn.fetch(
            "SELECT id, first_name, deposit_balance FROM users WHERE id = ANY($1::bigint[])",
            USER_IDS,
        )
        assert len(rows) == 4, f"expected 4 users, got {len(rows)}"
        print(f"  ✓ {len(rows)} users present")


async def create_habit(http: E2EHttp, owner: FakeUser) -> str:
    """Создать habit через /admin/v1/habits. Возвращает habit_id."""
    title = f"E2E-Subscription-Canonical-Prod-{RUN_TAG}"
    body = {
        "title": title,
        "description": "Pravki-subscription-2026-08-17 canonical order E2E",
        "stat_name": "Дисциплина",
        "stat_icon": "🔥",
        "chat_id": _CHAT_ID,
        "checkin_window_start": "00:00",
        "checkin_window_end": "23:59:59",
        "timezone": "Europe/Moscow",
        "proof_types": ["video_note"],
        "price_month": 50_00,  # 50₽
        "penalty_amount": 100_00,  # 100₽ — меньше deposit 200₽
        "stat_gain_per_checkin": 2,
        "stat_loss_per_miss": 1,
        "checkin_topic_link": f"https://t.me/c/{_SHORT_CHAT_ID}/1",
        "notifications_topic_link": f"https://t.me/c/{_SHORT_CHAT_ID}/2",
    }
    status, resp = await http.admin_post("/admin/v1/habits", owner=owner, json=body)
    assert status == 201, f"create habit failed: HTTP {status}, body={resp!r}"
    habit_id: str = resp["id"]
    print(f"  ✓ habit created: id={habit_id} title={title!r}")

    # Activate
    status, _ = await http.admin_post(
        f"/admin/v1/habits/{habit_id}/activate",
        owner=owner,
        json={"is_active": True},
    )
    assert status == 200, f"activate habit failed: HTTP {status}"
    print("  ✓ habit activated")
    return habit_id


async def join_users(http: E2EHttp, habit_id: str) -> dict[int, str]:
    """Каждый user делает topup + join. Возвращает {user_id: membership_id}."""
    print("=== Setup: topup + join for each user ===")
    out: dict[int, str] = {}
    for uid in USER_IDS:
        user = FakeUser(id=uid, first_name=f"E2E-{uid}", username=f"e2e_{uid}")
        status, resp = await http.api_post(
            "/api/v1/payments/topup",
            user=user,
            json={"amount_kopecks": 50_00},  # ещё +50₽ сверху initial 200�
        )
        assert status == 200, f"topup user {uid}: HTTP {status}, body={resp!r}"

        status, resp = await http.api_post(
            f"/api/v1/habits/{habit_id}/join", user=user, json={},
        )
        assert status == 200, f"join user {uid}: HTTP {status}, body={resp!r}"
        out[uid] = resp["id"]
        print(f"  ✓ user {uid}: membership {resp['id']}")
    return out


async def force_membership_state(
    db: E2EDatabase,
    *,
    membership_id: str,
    target_status: str,
    subscription_until: date | None,
) -> None:
    """Прямой SQL UPDATE для подготовки тестовых кейсов.

    subscription_until=None оставляет существующее значение (используется в
    case D где нужно оставить today).
    """
    async with db.session() as conn:
        if subscription_until is None:
            await conn.execute(
                "UPDATE memberships SET status = $1::membership_status "
                "WHERE id = $2::uuid",
                target_status,
                membership_id,
            )
        else:
            await conn.execute(
                "UPDATE memberships SET status = $1::membership_status, "
                "subscription_until = $2::date WHERE id = $3::uuid",
                target_status,
                subscription_until,
                membership_id,
            )


async def call_checkin_process(
    http: E2EHttp,
    *,
    user_id: int,
    chat_id: int,
) -> tuple[int, dict[str, Any]]:
    """Реальный HTTP POST → /internal/checkins/process через nginx.

    Bot обычно вызывает этот endpoint с реальным видео-кружком, но для E2E
    достаточно прямого вызова с service_token (X-Service-Token) — endpoint
    проверяет только membership state, не proof content (proof validation
    делается ботом pre-filter ДО этого endpoint).

    На самом деле proof валидация ЕСТЬ в endpoint'е (внутренний gate), но
    для нашего canonical-order теста proof не нужен — gate проверяет
    membership state ДО proof validation.
    """
    # Минимальный payload — реальный бот шлёт больше полей (proof_type,
    # message_id, message_sent_at), но для gate'а нужны только user_id и
    # chat_id (chat_id используется для поиска habit через chat_id → habit).
    payload = {
        "user_id": user_id,
        "chat_id": chat_id,
        "proof_type": "video_note",
        "message_id": 1,
        "message_sent_at": datetime.now().isoformat(),
    }
    status, body = await http.internal_post(
        "/internal/checkins/process", service="e2e-test", json=payload,
    )
    return status, body


async def run_canonical_order_real_e2e() -> int:
    print(f"RUN_TAG={RUN_TAG}")

    secrets = load_secrets()
    if not secrets.database_url:
        print("✗ DATABASE_URL not available", file=sys.stderr)
        return 1

    # Owner для admin endpoint
    owner = FakeUser(id=secrets.owner_telegram_id, first_name="Owner", username="owner")

    db = E2EDatabase(secrets.database_url)
    async with E2EHttp(
        base_url="https://api.prideclub.fun",
        bot_token=secrets.bot_token,
        bot_token_admin=secrets.bot_token_admin,
        webhook_secret=secrets.webhook_secret,
        service_secret=secrets.service_secret,
    ) as http:
        # Setup
        await setup_users(db)
        habit_id = await create_habit(http, owner)
        memberships = await join_users(http, habit_id)

        # Force membership states для каждого кейса
        yesterday = date.today() - timedelta(days=1)
        today = date.today()

        # Case A: status=ACTIVE + sub expired
        await force_membership_state(
            db, membership_id=memberships[USER_IDS[0]],
            target_status="active", subscription_until=yesterday,
        )
        # Case B: status=PAUSED + sub expired
        await force_membership_state(
            db, membership_id=memberships[USER_IDS[1]],
            target_status="paused", subscription_until=yesterday,
        )
        # Case C: status=LEFT + sub expired
        await force_membership_state(
            db, membership_id=memberships[USER_IDS[2]],
            target_status="left", subscription_until=yesterday,
        )
        # Case D: status=ACTIVE + sub today (last valid day)
        await force_membership_state(
            db, membership_id=memberships[USER_IDS[3]],
            target_status="active", subscription_until=today,
        )

        print()
        print("=== Tests (real HTTP via nginx → /internal/checkins/process) ===")

        results: list[CaseResult] = []

        # Case A
        http_status, body = await call_checkin_process(
            http, user_id=USER_IDS[0], chat_id=_CHAT_ID,
        )
        results.append(CaseResult(
            name="A: ACTIVE + sub expired → subscription_expired",
            expected_code="subscription_expired",
            actual_code=body.get("code") if isinstance(body, dict) else None,
            http_status=http_status,
            passed=(isinstance(body, dict) and body.get("code") == "subscription_expired" and body.get("ok") is False),
        ))

        # Case B
        http_status, body = await call_checkin_process(
            http, user_id=USER_IDS[1], chat_id=_CHAT_ID,
        )
        results.append(CaseResult(
            name="B: PAUSED + sub expired → subscription_expired (canonical #6 выше #7)",
            expected_code="subscription_expired",
            actual_code=body.get("code") if isinstance(body, dict) else None,
            http_status=http_status,
            passed=(isinstance(body, dict) and body.get("code") == "subscription_expired" and body.get("ok") is False),
        ))

        # Case C
        http_status, body = await call_checkin_process(
            http, user_id=USER_IDS[2], chat_id=_CHAT_ID,
        )
        results.append(CaseResult(
            name="C: LEFT + sub expired → subscription_expired (canonical #6 выше #8)",
            expected_code="subscription_expired",
            actual_code=body.get("code") if isinstance(body, dict) else None,
            http_status=http_status,
            passed=(isinstance(body, dict) and body.get("code") == "subscription_expired" and body.get("ok") is False),
        ))

        # Case D: sub today → check-in passes (send_task должен быть вызван)
        # Mock send_task чтобы не зависеть от worker'а на проде.
        from unittest.mock import patch
        # Patching на уровне модуля — но в async контексте через runtime.
        # Альтернатива: проверить только что gate не отверг (response.ok=True).
        # send_task вызовется через Celery producer — если broker недоступен,
        # вернётся ошибка. Для чистого E2E просто проверим ok=True.
        http_status, body = await call_checkin_process(
            http, user_id=USER_IDS[3], chat_id=_CHAT_ID,
        )
        results.append(CaseResult(
            name="D: ACTIVE + sub today → passes (день-в-день)",
            expected_code="ok",
            actual_code="ok" if (isinstance(body, dict) and body.get("ok") is True) else str(body.get("code") if isinstance(body, dict) else body),
            http_status=http_status,
            passed=(isinstance(body, dict) and body.get("ok") is True),
        ))

        # Bonus: верификация через прямой SQL что subscription_until реально
        # сохранилось в Postgres (не пропало при UPDATE).
        print()
        print("=== DB verify: subscription_until сохранён в Postgres ===")
        async with db.session() as conn:
            rows = await conn.fetch(
                "SELECT m.id::text, u.id::text AS user_id, m.status::text, m.subscription_until::text "
                "FROM memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.habit_id = $1::uuid ORDER BY u.id",
                habit_id,
            )
            for r in rows:
                print(f"  user={r['user_id']:>6} status={r['status']:<8} subscription_until={r['subscription_until']}")

        # Final report
        print()
        print("=== Итог ===")
        passed = 0
        for r in results:
            _check(r)
            if r.passed:
                passed += 1
        total = len(results)
        print(f"\n  {passed}/{total} проверок прошли")

        if passed == total:
            return 0
        else:
            return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run_canonical_order_real_e2e()))
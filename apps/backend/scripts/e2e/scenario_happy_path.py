"""Полный пользовательский путь, прогоняется end-to-end на проде.

Сценарий:
    PHASE A — клуб с always-open окном (00:00-23:59, Europe/Moscow):
        1. owner создаёт клуб через /admin/v1/habits
        2. owner активирует (POST .../activate)
        3. 3 synthetic-пользователя пополняют deposit (POST /payments/topup)
        4. каждый join'ит (POST /habits/{id}/join → ACTIVE membership)
        5. каждый шлёт video_note в /bot/webhook → bot prefilter PASS →
           /internal/checkins/process → worker → Checkin(status='done')
        6. SQL-снэпшот подтверждает deposit_balance, ACTIVE membership,
           Checkin запись.
    PHASE B — клуб с CLOSED окном (00:00-00:01):
        7. owner создаёт, активирует
        8. 3 пользователя join'ят
        9. user1 ловит user2 через POST /habits/{id}/catch
       10. SQL: Penalty (reason='caught'), Checkin (status='caught')
       11. user2 пытается video_note — bot prefilter отвергает с
           REJECT_CAUGHT_TODAY (видим через снэпшот: новая Checkin НЕ
           появляется; backend /internal/checkins/process НЕ вызывался
           — отлавливаем отсутствие новой строки).
       12. Финальный SQL-снэпшот.

Запуск:
    docker exec -i habit-backend bash -c 'cd /app/apps/backend && python -m scripts.e2e.scenario_happy_path'

Секреты читаются из /app/.env и /app/infra/.env (см. core.load_secrets).
Никакие секреты в stdout НЕ попадают.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time as _time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from scripts.e2e.auth import FakeUser
from scripts.e2e.core import E2EDatabase, E2EHttp, Secrets, load_secrets
from scripts.e2e.webhook import make_video_note_update


# --- конфиг -----------------------------------------------------------------


# Синтетические user_id. Берём из диапазона 99xxx (99001+), чтобы не
# пересекаться с seed-юзерами (10001..10005) и реальными прод-юзерами.
USERS: list[FakeUser] = [
    FakeUser(id=99001, first_name="E2E-Alice", username="e2e_alice"),
    FakeUser(id=99002, first_name="E2E-Bob", username="e2e_bob"),
    FakeUser(id=99003, first_name="E2E-Carol", username="e2e_carol"),
]

# Суммы в копейках (int — НЕ float).
PENALTY_A: int = 5_000   # 50 ₽
PENALTY_B: int = 10_000  # 100 ₽ (Phase B для catcher deposit share)
PENALTY_B_CLAMPED: int = 100_000  # 100 ₽ penalty с маленьким deposit (клэмп)
PRICE_MONTH_A: int = 50_000  # 500 �
DEPOSIT_TOPUP_KOPEKS: int = 200_00  # 200 ₽ на каждого
# Pravki-catcher-deposit (Phase 1 Task 1.3, 2026-08-21): доля ловцу.
# 30₽ из штрафа 100₽ — нормальный split (catcher_amount=3000, fund_share=7000).
CATCHER_B: int = 3_000   # 30 ₽
# Edge case: catcher > penalty → clamp к penalty на бэкенде в apply_catch
# (catcher_amount=10000, fund_share=0).
CATCHER_B_CLAMPED: int = 15_000  # 150 ₽ (>penalty)

RUN_TAG: str = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")


@dataclass
class HabitCtx:
    """Контекст созданного клуба для ассертов и отчёта."""

    habit_id: str
    title: str
    chat_id: int
    penalty_amount: int
    window_label: str  # для печати


# --- мелкие хелперы ----------------------------------------------------------


def make_topic_links(chat_id: int) -> tuple[str, str]:
    """Сгенерировать ссылки на checkin/notifications топики по chat_id.

    Формат URL: `https://t.me/c/<short_id>/<thread_id>` где short_id —
    «короткая» форма chat_id супергруппы (без префикса -100).
    Round-trip через `parse_telegram_topic_link` в
    `apps/backend/app/core/telegram_links.py` восстанавливает полный
    Bot API chat_id с префиксом `-100`.
    """
    if chat_id < -100_000_000_0000:
        short = -(chat_id + 100_000_000_0000)
    else:
        short = chat_id
    return (
        f"https://t.me/c/{short}/1",
        f"https://t.me/c/{short}/2",
    )


def _check(status: int, expected: int, where: str, body: Any = None) -> None:
    if status != expected:
        raise AssertionError(
            f"{where}: expected HTTP {expected}, got {status}, body={body!r}"
        )


def _check_in(value: Any, expected_set: set, where: str) -> None:
    if value not in expected_set:
        raise AssertionError(
            f"{where}: expected one of {expected_set}, got {value!r}"
        )


async def _wait_for_checkin_status(
    db: E2EDatabase,
    *,
    membership_id: str,
    want: str,
    timeout_sec: float = 15.0,
    poll_sec: float = 0.5,
) -> str | None:
    """Поллим checkin.status пока не получим нужный или таймаут."""
    today = datetime.now(tz=timezone.utc).date()
    deadline = _time.monotonic() + timeout_sec
    last: str | None = None
    while _time.monotonic() < deadline:
        async with db.session() as conn:
            last = await db.checkin_status(conn, membership_id=membership_id, on_date=today)
        if last == want:
            return last
        await asyncio.sleep(poll_sec)
    return last


async def _wait_for_penalty(
    db: E2EDatabase,
    *,
    membership_id: str,
    reason: str,
    timeout_sec: float = 15.0,
    poll_sec: float = 0.5,
) -> int:
    """Поллим пока не появится penalty нужного reason."""
    today = datetime.now(tz=timezone.utc).date()
    deadline = _time.monotonic() + timeout_sec
    last = 0
    while _time.monotonic() < deadline:
        async with db.session() as conn:
            last = await db.penalty_count(
                conn, membership_id=membership_id, on_date=today, reason=reason
            )
        if last > 0:
            return last
        await asyncio.sleep(poll_sec)
    return last


# --- сценарий ---------------------------------------------------------------


async def run_phase_a(
    secrets: Secrets,
    http: E2EHttp,
    db: E2EDatabase,
    owner: FakeUser,
) -> HabitCtx:
    print("\n=== PHASE A: always-open club ===")

    # 1. Create club
    title_a = f"E2E-A-Plank-{RUN_TAG}"
    # chat_id берём по RUN_TAG (timestamp) — уникально между прогонами.
    # Используем супергруппу с short_id <= 9 цифр чтобы не вылезать за типичный диапазон
    # Telegram: short_id = int(time.time()) % 10_000_000_000 (10 цифр макс).
    short_a = int(_time.time()) % 10_000_000_000
    chat_id_a = -100_000_000_0000 - short_a
    checkin_link_a, notif_link_a = make_topic_links(chat_id_a)
    body = {
        "title": title_a,
        "description": "E2E scenario, always-open window",
        "stat_name": "Дисциплина",
        "stat_icon": "🔥",
        "chat_id": chat_id_a,
        "checkin_window_start": "00:00",
        "checkin_window_end": "23:59:59",
        "timezone": "Europe/Moscow",
        "proof_types": ["video_note"],
        "price_month": PRICE_MONTH_A,
        "penalty_amount": PENALTY_A,
        "stat_gain_per_checkin": 2,
        "stat_loss_per_miss": 1,
        "checkin_topic_link": checkin_link_a,
        "notifications_topic_link": notif_link_a,
    }
    status, resp = await http.admin_post(
        "/admin/v1/habits", owner=owner, json=body
    )
    _check(status, 201, "create habit A", body=resp)
    habit_id: str = resp["id"]
    _check_in(resp["is_active"], {False}, "habit A.is_active at create")
    print(f"  [1] created: id={habit_id} title={title_a!r}")

    # 2. Activate
    status, _ = await http.admin_post(
        f"/admin/v1/habits/{habit_id}/activate",
        owner=owner,
        json={"is_active": True},
    )
    _check(status, 200, "activate habit A")
    print("  [2] activated")

    # 3. Topup deposit for each user
    # POST /api/v1/payments/topup (TopupRequest):
    #   habit_id: optional
    #   amount_kopecks: gt=0, le=10M
    # Mock-платёж — каждый charge_id уникальный (uuid4 на сервере).
    for u in USERS:
        status, resp = await http.api_post(
            "/api/v1/payments/topup",
            user=u,
            json={
                "amount_kopecks": DEPOSIT_TOPUP_KOPEKS,
            },
        )
        _check(status, 200, f"topup user {u.id}", body=resp)
    print(f"  [3] topup +{DEPOSIT_TOPUP_KOPEKS / 100:.2f}₽ × {len(USERS)} users")

    # 4. Each user joins
    membership_ids_a: list[str] = []
    for u in USERS:
        status, resp = await http.api_post(
            f"/api/v1/habits/{habit_id}/join", user=u, json={}
        )
        _check(status, 200, f"join user {u.id}")
        membership_ids_a.append(resp["id"])
    print(f"  [4] joined: {len(membership_ids_a)} memberships")

    # Verify memberships ACTIVE
    async with db.session() as conn:
        for u, mid in zip(USERS, membership_ids_a):
            ms = await db.membership_status(conn, user_id=u.id, habit_id=habit_id)
            _check_in(
                ms, {"active"}, f"membership status user {u.id}"
            )
    print("  [4v] DB: all memberships ACTIVE")

    # 5. Each user sends video_note to /bot/webhook
    for u, mid in zip(USERS, membership_ids_a):
        update = make_video_note_update(
            chat_id=chat_id_a,
            user=u,
            duration_seconds=4,
            message_thread_id=1,  # в checkin-топик
        )
        status, _ = await http.webhook_post(update)
        _check(status, 200, f"webhook video_note user {u.id}")

        got = await _wait_for_checkin_status(
            db, membership_id=mid, want="done"
        )
        if got != "done":
            raise AssertionError(
                f"user {u.id}: expected checkin.status='done', got {got!r}"
            )
    print("  [5] video_note accepted × 3, DB Checkin(status='done') ✓")

    return HabitCtx(
        habit_id=habit_id,
        title=title_a,
        chat_id=chat_id_a,
        penalty_amount=PENALTY_A,
        window_label="00:00-23:59:59 (always open in Europe/Moscow)",
    )


async def run_phase_b(
    secrets: Secrets,
    http: E2EHttp,
    db: E2EDatabase,
    owner: FakeUser,
) -> tuple[HabitCtx, list[str]]:
    print("\n=== PHASE B: closed-window club + catch flow ===")

    # 7. Create habit B with closed window (00:00-00:01)
    title_b = f"E2E-B-ClosedWindow-{RUN_TAG}"
    short_b = int(_time.time()) % 10_000_000_000
    chat_id_b = -100_000_000_0000 - short_b
    checkin_link_b, notif_link_b = make_topic_links(chat_id_b)
    body = {
        "title": title_b,
        "description": "E2E scenario, window 00:00-00:01 (closed at test time)",
        "stat_name": "Дисциплина",
        "stat_icon": "🔥",
        "chat_id": chat_id_b,
        "checkin_window_start": "00:00",
        "checkin_window_end": "00:01",
        "timezone": "Europe/Moscow",
        "proof_types": ["video_note"],
        "price_month": PRICE_MONTH_A,
        "penalty_amount": PENALTY_B,
        # Pravki-catcher-deposit (Phase 1 Task 1.3): доля ловцу 30₽ из 100₽
        # штрафа. catcher_amount_kopecks=3000 (нормальный split, не clamp).
        "catcher_amount_kopecks": CATCHER_B,
        "stat_gain_per_checkin": 2,
        "stat_loss_per_miss": 1,
        "checkin_topic_link": checkin_link_b,
        "notifications_topic_link": notif_link_b,
    }
    status, resp = await http.admin_post(
        "/admin/v1/habits", owner=owner, json=body
    )
    _check(status, 201, "create habit B")
    habit_id_b: str = resp["id"]
    print(f"  [7] created: id={habit_id_b}")

    # 8. Activate + join (deposit уже есть с Phase A)
    status, _ = await http.admin_post(
        f"/admin/v1/habits/{habit_id_b}/activate",
        owner=owner,
        json={"is_active": True},
    )
    _check(status, 200, "activate habit B")

    membership_ids_b: list[str] = []
    for u in USERS:
        status, resp = await http.api_post(
            f"/api/v1/habits/{habit_id_b}/join", user=u, json={}
        )
        _check(status, 200, f"join habit B user {u.id}")
        membership_ids_b.append(resp["id"])
    print(f"  [8] joined: {len(membership_ids_b)} memberships")

    # 9. User 1 (99001) catches User 2 (99002)
    catcher, victim = USERS[0], USERS[1]
    victim_membership = membership_ids_b[1]
    catch_payload = {"violator_membership_id": victim_membership}
    # Pravki-catcher-deposit (Phase 1 Этап 5): снимок deposit ДО catch
    # (юзеры 99001+ переиспользуются между прогонами, deposit накапливается —
    # проверяем ДЕЛЬТУ, а не абсолютное значение).
    async with db.session() as conn:
        catcher_balance_before = await db.deposit_balance(
            conn, user_id=catcher.id
        )
        victim_balance_before = await db.deposit_balance(
            conn, user_id=victim.id
        )
        prize_before = await db.prize_pool(conn, habit_id=habit_id_b)
    status, resp = await http.api_post(
        f"/api/v1/habits/{habit_id_b}/catch",
        user=catcher,
        json=catch_payload,
    )
    _check(status, 200, "catch endpoint")
    if not resp.get("ok"):
        raise AssertionError(
            f"catch failed: ok=False, code={resp.get('code')}, body={resp}"
        )
    print(f"  [9] catch: catcher={catcher.id} -> victim={victim.id}, "
          f"amount={resp.get('amount')}")

    # 10. Verify penalty + checkin(status='caught') для жертвы
    got = await _wait_for_penalty(
        db, membership_id=victim_membership, reason="caught"
    )
    if got != 1:
        raise AssertionError(
            f"expected exactly 1 penalty(reason='caught'), got {got}"
        )
    async with db.session() as conn:
        v_status = await db.checkin_status(
            conn, membership_id=victim_membership,
            on_date=datetime.now(tz=timezone.utc).date(),
        )
        _check_in(v_status, {"caught"}, "victim checkin.status after catch")

        # Pravki-catcher-deposit (Phase 1 Task 1.3): детали Penalty — split.
        # penalty=10000 (100₽), catcher_amount_kopecks=3000 (30₽) →
        # catcher_amount=3000, fund_share=7000.
        p = await db.penalty_detail(
            conn, membership_id=victim_membership,
            on_date=datetime.now(tz=timezone.utc).date(),
        )
        if p is None:
            raise AssertionError("penalty_detail returned None (no caught penalty)")
        assert p["amount"] == PENALTY_B, (
            f"Penalty.amount expected {PENALTY_B}, got {p['amount']}"
        )
        assert p["catcher_amount"] == CATCHER_B, (
            f"Penalty.catcher_amount expected {CATCHER_B}, "
            f"got {p['catcher_amount']}"
        )
        assert p["fund_share"] == PENALTY_B - CATCHER_B, (
            f"Penalty.fund_share expected {PENALTY_B - CATCHER_B} "
            f"({PENALTY_B} - {CATCHER_B}), got {p['fund_share']}"
        )
        assert p["is_suspicious_pair"] is False, (
            f"Penalty.is_suspicious_pair expected False, "
            f"got {p['is_suspicious_pair']} (новые FakeUser не должны быть flagged)"
        )
        # Инвариант CHECK ck_penalties_amount_equals_sum (миграция 017):
        # amount = catcher_amount + fund_share
        if p["amount"] != p["catcher_amount"] + p["fund_share"]:
            raise AssertionError(
                f"CHECK violation: amount({p['amount']}) != "
                f"catcher_amount({p['catcher_amount']}) + "
                f"fund_share({p['fund_share']})"
            )

        # Pravki-catcher-deposit: дельта deposit_balance (юзеры 99001+
        # переиспользуются, deposit накапливается — проверяем дельту, а не
        # абсолют).
        catcher_balance = await db.deposit_balance(conn, user_id=catcher.id)
        victim_balance = await db.deposit_balance(conn, user_id=victim.id)
        catcher_delta = catcher_balance - catcher_balance_before
        victim_delta = victim_balance - victim_balance_before
        if catcher_delta != CATCHER_B:
            raise AssertionError(
                f"catcher.deposit_balance delta expected +{CATCHER_B}, "
                f"got {catcher_delta} ({catcher_balance_before} → {catcher_balance})"
            )
        if victim_delta != -PENALTY_B:
            raise AssertionError(
                f"victim.deposit_balance delta expected -{PENALTY_B}, "
                f"got {victim_delta} ({victim_balance_before} → {victim_balance})"
            )

        # Pravki-catcher-deposit: prize_pool вырос на fund_share.
        prize = await db.prize_pool(conn, habit_id=habit_id_b)
        prize_delta = prize - prize_before
        # Phase B — единственный catch в этом клубе за прогон,
        # prize_pool должен вырасти на fund_share (= PENALTY_B - CATCHER_B).
        expected_prize_delta = PENALTY_B - CATCHER_B
        if prize_delta != expected_prize_delta:
            raise AssertionError(
                f"Habit.prize_pool delta expected +{expected_prize_delta} "
                f"({PENALTY_B} penalty - {CATCHER_B} catcher), "
                f"got {prize_delta} ({prize_before} → {prize})"
            )

        # Печать фактических значений для верификации (как просил Дмитрий)
        print("  [10] DB: Penalty(reason='caught') ✓ "
              "Checkin(status='caught') ✓")
        print(f"       Penalty.amount          = {p['amount']:>6} коп "
              f"({p['amount'] / 100:>5.2f}₽)")
        print(f"       Penalty.catcher_amount   = {p['catcher_amount']:>6} коп "
              f"({p['catcher_amount'] / 100:>5.2f}₽)  [expected {CATCHER_B}]")
        print(f"       Penalty.fund_share       = {p['fund_share']:>6} коп "
              f"({p['fund_share'] / 100:>5.2f}₽)  "
              f"[expected {PENALTY_B - CATCHER_B}]")
        print(f"       Penalty.is_suspicious_pair = {p['is_suspicious_pair']}  "
              f"[expected False]")
        print(f"       catcher.deposit_balance = {catcher_balance_before:>6} → "
              f"{catcher_balance:>6} коп "
              f"(Δ +{catcher_delta}, expected +{CATCHER_B})")
        print(f"       victim.deposit_balance  = {victim_balance_before:>6} → "
              f"{victim_balance:>6} коп "
              f"(Δ {victim_delta}, expected -{PENALTY_B})")
        print(f"       Habit.prize_pool        = {prize_before:>6} → "
              f"{prize:>6} коп "
              f"(Δ +{prize_delta}, expected +{expected_prize_delta})")

    # 11. Victim пытается video_note — bot prefilter должен REJECT caught_today.
    # Бот шлёт в фейк-чат send_message → fail, но HTTP 200 от webhook всё равно.
    # Проверяем, что новая Checkin строка НЕ появилась (bot не довёл до backend).
    checkin_id_before = await _fetch_checkin_id(
        db, membership_id=victim_membership
    )

    update = make_video_note_update(
        chat_id=chat_id_b,
        user=victim,
        duration_seconds=4,
        message_thread_id=1,
    )
    status, _ = await http.webhook_post(update)
    _check(status, 200, "victim's webhook after catch")

    await asyncio.sleep(2.0)  # даём worker время (если бы он вызывался)
    checkin_id_after = await _fetch_checkin_id(
        db, membership_id=victim_membership
    )
    if checkin_id_after != checkin_id_before:
        raise AssertionError(
            f"victim's checkin unexpectedly changed: "
            f"before={checkin_id_before} after={checkin_id_after} "
            f"(bot should have rejected — no new checkin expected)"
        )
    print("  [11] victim retry: bot prefilter rejected (caught_today) "
          "→ no new Checkin ✓")
    print(f"       HTTP {status} from webhook (aiogram always 200)")

    return (
        HabitCtx(
            habit_id=habit_id_b,
            title=title_b,
            chat_id=chat_id_b,
            penalty_amount=PENALTY_B,
            window_label="00:00-00:01 (closed at test time, Europe/Moscow)",
        ),
        membership_ids_b,
    )


async def _fetch_checkin_id(
    db: E2EDatabase,
    *,
    membership_id: str,
) -> str | None:
    import asyncpg  # локальный импорт чтобы core не зависел

    async with db.session() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM checkins "
            "WHERE membership_id = $1::uuid AND date = CURRENT_DATE "
            "ORDER BY verified_at DESC LIMIT 1",
            membership_id,
        )
    return str(row["id"]) if row else None


# --- main --------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="e2e scenario — full user journey simulation"
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="После успешного прогона вызвать cleanup.py --apply "
        "(удаляет все артефакты с префиксом E2E- из текущего run_tag).",
    )
    args = parser.parse_args()

    print(f"e2e scenario_happy_path — run_tag={RUN_TAG}")
    secrets = load_secrets()

    owner = FakeUser(
        id=secrets.owner_telegram_id,
        first_name="E2EOwner",
        username="e2eowner",
    )

    async with E2EHttp(
        base_url=secrets.backend_url,
        bot_token=secrets.bot_token,
        bot_token_admin=secrets.bot_token_admin,
        webhook_secret=secrets.webhook_secret,
        service_secret=secrets.service_secret,
    ) as http:
        ctx_a = await run_phase_a(secrets, http, E2EDatabase(secrets.database_url), owner)
        ctx_b, _memberships_b = await run_phase_b(
            secrets, http, E2EDatabase(secrets.database_url), owner
        )

    # Финальный SQL-снэпшот
    db = E2EDatabase(secrets.database_url)
    print("\n=== final SQL snapshot ===")
    async with db.session() as conn:
        for label, h in (("A", ctx_a), ("B", ctx_b)):
            meta = await db.habit_meta(conn, habit_id=h.habit_id)
            print(
                f"habit {label}: id={meta['id']} title={meta['title']!r} "
                f"chat_id={meta['chat_id']} active={meta['is_active']} "
                f"penalty={meta['penalty_amount'] / 100:.2f}₽ "
                f"window={meta['w_start']}-{meta['w_end']}"
            )

        print("\nmemberships + checkins + penalties (today):")
        rows = await conn.fetch(
            """
            SELECT u.id AS user_id, u.first_name, m.habit_id, m.status::text AS ms,
                   c.status::text AS checkin_status, c.date AS checkin_date,
                   (SELECT COUNT(*) FROM penalties p
                     WHERE p.membership_id = m.id AND p.date = CURRENT_DATE) AS penalties_n,
                   (SELECT COALESCE(SUM(amount),0) FROM penalties p
                     WHERE p.membership_id = m.id AND p.date = CURRENT_DATE) AS penalties_amt
            FROM users u
            JOIN memberships m ON m.user_id = u.id
            LEFT JOIN checkins c
              ON c.membership_id = m.id AND c.date = CURRENT_DATE
            WHERE u.id = ANY($1::bigint[])
              AND m.habit_id = ANY($2::uuid[])
            ORDER BY u.id, m.habit_id
            """,
            [u.id for u in USERS],
            [ctx_a.habit_id, ctx_b.habit_id],
        )
        for r in rows:
            print(
                f"  user={r['user_id']} ({r['first_name']}) "
                f"habit={r['habit_id']} ms={r['ms']} "
                f"checkin={r['checkin_status']} "
                f"penalties_today={r['penalties_n']} "
                f"({r['penalties_amt'] / 100:.2f}₽)"
            )

    print("\n=== ALL ASSERTS PASSED ✓ ===")
    print(f"A: {ctx_a.habit_id}  {ctx_a.title}")
    print(f"B: {ctx_b.habit_id}  {ctx_b.title}")

    if args.cleanup:
        print("\n=== cleanup (--cleanup flag set) ===")
        from scripts.e2e.cleanup import _delete_targets, _print_plan, _collect_targets

        summary = await _collect_targets(E2EDatabase(secrets.database_url), run_tag=RUN_TAG)
        _print_plan(summary)
        if any(summary.values()):
            counts = await _delete_targets(
                E2EDatabase(secrets.database_url), run_tag=RUN_TAG
            )
            print(f"cleanup applied for run_tag={RUN_TAG}: {counts}")
        else:
            print("nothing to cleanup for this run_tag.")

    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)

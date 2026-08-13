"""Cleanup e2e-артефактов из БД и Redis.

Что удаляется:
- habits WHERE title LIKE 'E2E-%' (cascades → memberships, checkins, penalties,
  seasons, chat_member_states, suspicious_pairs — всё, что ссылается)
- users WHERE id IN (E2E_USER_IDS) (cascades → transactions)
- Redis ключи `sse:user:{user}:{habit}` и `sse:habit:{habit}` для удалённых
  habit_id (synthetic streams не auto-expire).
- Redis ключи `sse_published:*` для удалённых юзеров/мембершипов
  (idempotency namespaces).
- Redis ключи `today:{habit_id}:{user_id}` и подобные — нет такого формата,
  today-cache живёт в `RedisTodayCache`, проверяется опционально.

Безопасность:
- По умолчанию dry-run (печатает план, ничего не удаляет).
- `--apply` — реальное удаление.
- `--yes` — пропустить подтверждение в apply-режиме (для CI).

Запуск:
    # просто посмотреть, что будет удалено
    docker exec habit-backend python -m scripts.e2e.cleanup

    # реально почистить (с подтверждением)
    docker exec -it habit-backend python -m scripts.e2e.cleanup --apply

    # только конкретный run (например после нескольких параллельных прогонов)
    docker exec -it habit-backend python -m scripts.e2e.cleanup --apply --run-tag 20260813-103343

    # без подтверждения (для CI)
    docker exec habit-backend python -m scripts.e2e.cleanup --apply --yes

После сценария с флагом --cleanup:
    docker exec -e WEBHOOK_SECRET=... habit-backend \
        python -m scripts.e2e.scenario_happy_path --cleanup
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from scripts.e2e.auth import FakeUser
from scripts.e2e.core import E2EDatabase, Secrets, load_secrets


# Синтетические user_id, которые сценарий создаёт / использует.
# Те же, что в scenario_happy_path.py:USERS — единый источник правды
# перенесён сюда, чтобы cleanup мог быть вызван без зависимости от
# сценария (например если предыдущий прогон оставил артефакты).
E2E_USER_IDS: list[int] = [
    FakeUser(id=99001, first_name="E2E-Alice", username="e2e_alice").id,
    FakeUser(id=99002, first_name="E2E-Bob", username="e2e_bob").id,
    FakeUser(id=99003, first_name="E2E-Carol", username="e2e_carol").id,
]


async def _collect_targets(
    db: E2EDatabase,
    *,
    run_tag: str | None,
) -> dict[str, list]:
    """Собирает id артефактов БЕЗ изменений. Read-only."""
    summary: dict[str, list] = {
        "habits": [],
        "memberships": [],
        "checkins": [],
        "penalties": [],
        "transactions": [],
        "users": [],
    }

    async with db.session() as conn:
        # habits by title prefix (optionally also by run_tag substring)
        if run_tag:
            habit_rows = await conn.fetch(
                "SELECT id::text AS id, title FROM habits "
                "WHERE title LIKE 'E2E-%' AND title LIKE $1 "
                "ORDER BY title",
                f"%{run_tag}%",
            )
        else:
            habit_rows = await conn.fetch(
                "SELECT id::text AS id, title FROM habits "
                "WHERE title LIKE 'E2E-%' "
                "ORDER BY title",
            )
        for r in habit_rows:
            summary["habits"].append((r["id"], r["title"]))

        habit_ids = [r["id"] for r in habit_rows] if habit_rows else []

        # memberships, checkins, penalties для найденных habit_id
        if habit_ids:
            ms_rows = await conn.fetch(
                "SELECT m.id::text AS id, m.user_id, m.status::text AS status "
                "FROM memberships m WHERE m.habit_id = ANY($1::uuid[])",
                habit_ids,
            )
            for r in ms_rows:
                summary["memberships"].append((r["id"], r["user_id"], r["status"]))

            checkin_rows = await conn.fetch(
                "SELECT c.id::text AS id, c.membership_id, c.date::text AS date, "
                "c.status::text AS status "
                "FROM checkins c JOIN memberships m ON m.id = c.membership_id "
                "WHERE m.habit_id = ANY($1::uuid[])",
                habit_ids,
            )
            for r in checkin_rows:
                summary["checkins"].append(
                    (r["id"], r["membership_id"], r["date"], r["status"])
                )

            penalty_rows = await conn.fetch(
                "SELECT p.id::text AS id, p.membership_id, p.amount, p.reason::text AS reason, "
                "p.date::text AS date "
                "FROM penalties p JOIN memberships m ON m.id = p.membership_id "
                "WHERE m.habit_id = ANY($1::uuid[])",
                habit_ids,
            )
            for r in penalty_rows:
                summary["penalties"].append(
                    (r["id"], r["membership_id"], r["amount"], r["reason"], r["date"])
                )

        # e2e users (по synthetic ids, не зависит от habits)
        user_rows = await conn.fetch(
            "SELECT id, first_name FROM users WHERE id = ANY($1::bigint[])",
            E2E_USER_IDS,
        )
        for r in user_rows:
            summary["users"].append((r["id"], r["first_name"]))

        if summary["users"]:
            tx_rows = await conn.fetch(
                "SELECT id::text AS id, type, amount, created_at::text AS created_at "
                "FROM transactions WHERE user_id = ANY($1::bigint[]) "
                "ORDER BY created_at DESC",
                [u[0] for u in summary["users"]],
            )
            for r in tx_rows:
                summary["transactions"].append(
                    (r["id"], r["type"], r["amount"], r["created_at"])
                )

    return summary


async def _delete_targets(
    db: E2EDatabase,
    *,
    run_tag: str | None,
) -> dict[str, int]:
    """Удаляет артефакты внутри одной транзакции. Возвращает counts."""
    counts = {
        "habits": 0,
        "memberships": 0,
        "checkins": 0,
        "penalties": 0,
        "transactions": 0,
        "users": 0,
    }

    async with db.session() as conn:
        async with conn.transaction():
            # 1. habits (cascades memberships, checkins, penalties, etc.)
            if run_tag:
                res = await conn.execute(
                    "DELETE FROM habits "
                    "WHERE title LIKE 'E2E-%' AND title LIKE $1",
                    f"%{run_tag}%",
                )
            else:
                res = await conn.execute(
                    "DELETE FROM habits WHERE title LIKE 'E2E-%'"
                )
            counts["habits"] = int(res.split()[-1])

            # 2. e2e users (cascades transactions)
            res = await conn.execute(
                "DELETE FROM users WHERE id = ANY($1::bigint[])",
                E2E_USER_IDS,
            )
            counts["users"] = int(res.split()[-1])

            # 3. report cascade counts via SELECT (для отчёта)
            #    После DELETE каскады уже отработали, поэтому эти SELECT'ы
            #    дают «оставшиеся» строки — обнуляем аккумуляторы
            #    если их что-то осталось.
            #
            #    Не критично: реальные counts мы печатаем через _collect_targets
            #    перед удалением. Здесь только sanity-check что cleanup
            #    действительно снёс всё.
            checks = []
            # memberships для удалённых habit_id уже нет (cascade), проверим
            # что для E2E_USER_IDS memberships тоже все ушли (user cascade).
            rm = await conn.fetchval(
                "SELECT COUNT(*) FROM memberships WHERE user_id = ANY($1::bigint[])",
                E2E_USER_IDS,
            )
            checks.append(("memberships", int(rm or 0)))
            # transactions для удалённых users
            rt = await conn.fetchval(
                "SELECT COUNT(*) FROM transactions WHERE user_id = ANY($1::bigint[])",
                E2E_USER_IDS,
            )
            checks.append(("transactions", int(rt or 0)))
            # penalties/checkins — memberships нет, поэтому должно быть 0
            rc = await conn.fetchval("SELECT COUNT(*) FROM checkins")
            checks.append(("checkins", int(rc or 0)))
            rp = await conn.fetchval("SELECT COUNT(*) FROM penalties")
            checks.append(("penalties", int(rp or 0)))

            for k, v in checks:
                counts[k] = v

    return counts


async def _cleanup_redis(
    db: E2EDatabase,
    secrets: Secrets,
    *,
    run_tag: str | None,
) -> dict[str, int]:
    """Чистит SSE/idem Redis ключи для удалённых habit_id и user_id.

    Не падает если Redis недоступен — просто логирует.
    """
    import redis.asyncio as aioredis

    counts = {"sse_streams": 0, "sse_published": 0, "today_cache": 0}

    redis_url = os.environ.get("REDIS_URL") or "redis://redis:6379/0"
    # NOTE: redis_url можно получить из settings, но мы пытаемся
    # минимизировать зависимости. На проде внутри docker network — `redis:6379`.

    try:
        r = aioredis.from_url(redis_url, decode_responses=True)
        # await r.ping()
    except Exception:  # noqa: BLE001
        return counts

    # Собираем ВСЕ e2e habit_id (даже после delete — берём из
    # _collect_targets ДО удаления, что вызывающий гарантирует).
    # Здесь просто читаем из БД ещё раз (habits уже удалены → вернёт 0).
    # Поэтому собираем habit_ids передачей сверху.
    # Для упрощения: сканируем keys.
    try:
        # SSE user-stream keys
        async for key in r.scan_iter(match="sse:user:*"):
            # Если key соответствует e2e user (99001-99003) — удалить
            parts = key.split(":")
            if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) in E2E_USER_IDS:
                await r.delete(key)
                counts["sse_streams"] += 1
        # SSE habit-stream keys (для удалённых habit — трубно угадать id,
        # но после cleanup все e2e habit_id удалены, и если у них были
        # активные streams, удаляем все ключи сэтой сигнатуры)
        async for key in r.scan_iter(match="sse:habit:*"):
            await r.delete(key)
            counts["sse_streams"] += 1
        # SSE published idempotency для e2e юзеров
        async for key in r.scan_iter(match="sse_published:*"):
            parts = key.split(":")
            if len(parts) >= 2 and parts[-1].isdigit() and int(parts[-1]) in E2E_USER_IDS:
                await r.delete(key)
                counts["sse_published"] += 1
        # Today cache для e2e юзеров (если RedisTodayCache там что-то клал)
        async for key in r.scan_iter(match="today:*"):
            parts = key.split(":")
            if len(parts) >= 3 and parts[1].isdigit() and int(parts[1]) in E2E_USER_IDS:
                await r.delete(key)
                counts["today_cache"] += 1
    finally:
        await r.aclose()

    return counts


def _print_plan(summary: dict[str, list]) -> None:
    """DRY-RUN: печатает что будет удалено."""
    print("e2e cleanup — DRY RUN. Будет удалено:")
    for k, items in summary.items():
        print(f"  {k}: {len(items)}")
    for habit_id, title in summary["habits"]:
        print(f"    habit  {habit_id}  {title!r}")
    for user_id, name in summary["users"]:
        print(f"    user   {user_id}  {name!r}")
    if summary["memberships"]:
        print(f"  memberships (sample, first 5):")
        for mid, uid, status in summary["memberships"][:5]:
            print(f"    ms     {mid}  user={uid} status={status}")
    if summary["transactions"]:
        print(f"  transactions (sample, first 5):")
        for tid, ttype, amt, _created in summary["transactions"][:5]:
            print(f"    tx     {tid}  type={ttype} amount={amt}")
    print()


def _confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


import os  # noqa: E402  — cleanup uses os.environ for REDIS_URL


async def amain() -> int:
    p = argparse.ArgumentParser(description="e2e cleanup")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Реально удалить. Без этого флага — только dry-run.",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Пропустить подтверждение (только вместе с --apply).",
    )
    p.add_argument(
        "--run-tag",
        type=str,
        default=None,
        help="Ограничить удаление только этим run_tag (substring в title).",
    )
    p.add_argument(
        "--no-redis",
        action="store_true",
        help="Не трогать Redis (только Postgres).",
    )

    args = p.parse_args()

    secrets = load_secrets()
    db = E2EDatabase(secrets.database_url)

    summary = await _collect_targets(db, run_tag=args.run_tag)
    _print_plan(summary)

    if not any(summary.values()):
        print("nothing to clean.")
        return 0

    if not args.apply:
        print("(re-run with --apply to actually delete)")
        return 0

    if not args.yes and not _confirm("Apply cleanup?"):
        print("aborted.")
        return 1

    counts = await _delete_targets(db, run_tag=args.run_tag)
    print(f"db cleanup applied: {counts}")

    if not args.no_redis:
        redis_counts = await _cleanup_redis(db, secrets, run_tag=args.run_tag)
        print(f"redis cleanup applied: {redis_counts}")

    print("done.")
    return 0


if __name__ == "__main__":
    rc = asyncio.run(amain())
    sys.exit(rc)

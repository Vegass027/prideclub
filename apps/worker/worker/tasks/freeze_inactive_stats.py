"""Pravki §Phase 3 Task 3.5 — cron worker для заморозки неактивных
характеристик (frozen after 30 days без чек-инов).

Per Dmitry 21.08.2026 архитектура:
- Каждый batch — своя AsyncSession + transaction. Session открывается,
  делается list_for_freeze_batch + bulk_freeze + commit, ЗАКРЫВАЕТСЯ.
  Следующая итерация — новая session с гарантированно свежим
  состоянием.
- Bulk_freeze с defensive guard is_frozen=false (Task 3.2 fix 3) —
  rowcount может быть < len(batch_ids) при конкурентном freeze.
  Worker логирует именно rowcount, не размер batch.
- Empty batch → break.
- Explicit try/except + rollback (per Dmitry 21.08.2026) — auto-
  rollback не гарантируется через AsyncSession.close().
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.core.constants import CharacterConfig
from app.core.logging import get_logger
from app.repositories.user_stats_repository import UserStatsRepository
from db.session import async_session_factory  # type: ignore[import-not-found]

try:
    from worker.celery_app import celery_app  # type: ignore[import-not-found]
except ImportError:
    celery_app = None  # type: ignore

logger = get_logger("worker.freeze_inactive_stats")


async def _process(
    *,
    session_factory=None,
) -> dict[str, Any]:
    """Чистая async-функция для тестов и для Celery-обёртки run().

    Worker-driven top-N loop, per-batch session lifecycle (Phase 3
    Task 3.5, per Dmitry 21.08.2026).

    Returns: dict {batches, frozen_total, threshold_days, batch_size}.
    """
    factory = (
        session_factory if session_factory is not None
        else async_session_factory
    )

    threshold_days = CharacterConfig.FREEZE_AFTER_DAYS_INACTIVE
    batch_size = CharacterConfig.FREEZE_CRON_BATCH
    reason = CharacterConfig.DEFAULT_FROZEN_REASON

    frozen_total = 0
    batches = 0

    while True:
        async with factory() as session:
            user_stats_repo = UserStatsRepository(session)

            batch_ids = await user_stats_repo.list_for_freeze_batch(
                threshold_days=threshold_days, batch=batch_size,
            )
            if not batch_ids:
                # Empty batch — выходим, session closes on context exit.
                break

            try:
                affected = await user_stats_repo.bulk_freeze(batch_ids, reason)
                await session.commit()
            except Exception:
                # Per Dmitry 21.08.2026: explicit rollback + raise.
                # Auto-rollback через AsyncSession.close() не гарантирован
                # в SQLAlchemy 2.0 — фиксируем явно для cron-устойчивости.
                await session.rollback()
                raise

            if affected != len(batch_ids):
                # Defensive: WHERE is_frozen=false в bulk_freeze
                # исключает race с другим cron-инстансом. Если
                # affected < batch_size — другая транзакция
                # успела заморозить часть. Фиксируем для диагностики.
                logger.warning(
                    "freeze_batch_partial",
                    extra={
                        "batch_no": batches + 1,
                        "expected": len(batch_ids),
                        "affected": affected,
                    },
                )

            # Counter обновляем ДО log (per Dmitry 21.08.2026).
            batches += 1
            frozen_total += affected

        # Log AFTER session closes — durable separation: даже
        # если worker crash'нет после закрытия сессии и до log entry,
        # данные уже durably committed. Следующий cron-run увидит
        # frozen rows и skip'нет (filter is_frozen=false).
        logger.info(
            "freeze_batch_done",
            extra={
                "batch_no": batches,
                "batch_size": len(batch_ids),
                "affected": affected,
                "frozen_total": frozen_total,
            },
        )

    logger.info(
        "freeze_inactive_stats_done",
        extra={
            "batches": batches,
            "frozen_total": frozen_total,
            "threshold_days": threshold_days,
            "batch_size": batch_size,
        },
    )
    return {
        "batches": batches,
        "frozen_total": frozen_total,
        "threshold_days": threshold_days,
        "batch_size": batch_size,
    }


# ── Celery entry (production runtime) ─────────────────────────
# ⚠️ Per Dmitry 21.08.2026: Celery не await'ит произвольные `async def`.
# Sync wrapper использует asyncio.run() чтобы драйвить async loop.
# Паттерн один-в-один с update_user_photos.py (см. apps/worker/worker/tasks/update_user_photos.py:408).
if celery_app is not None:

    @celery_app.task(name="worker.tasks.freeze_inactive_stats.run")
    def run() -> dict[str, Any]:
        return asyncio.run(_process())
else:
    # Вне Celery (тесты без worker fixtures) — async run, tests await'ят напрямую.
    run = _process

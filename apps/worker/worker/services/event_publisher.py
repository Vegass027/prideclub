"""Worker-сервис публикации событий чек-ина в Redis Streams для SSE.

Шаг 3 плана `sse+redis.md` (редакция 5). Шаг 4 (backend XREAD) будет
потрелять из этих стримов — см. `apps/backend/app/services/sse/redis_stream_bus.py`.

Архитектурное место: worker-уровень (не backend), потому что:
- События публикуются СТРОГО после успешного commit в БД (task `process_checkin`).
- Если backend уронит commit — событие не должно уйти (Guard 1 в _process).
- Worker уже единственная точка синхронизации для дедупликации задачи.

Идемпотентность публикации (Guard 2, см. план §2.3):
- Ключ:    ``sse_published:checkin:{membership_id}:{date}``.
- Механика: ``SET key "1" NX EX 86400`` через тот же async redis-клиент.
- Если вернул ``None`` (ключ уже есть) — повторная Celery-доставка →
  XADD не выполняется, возвращается ``False``.
- TTL 24 часа покрывает полный день (с учётом TZ клубов, ±14 ч) плюс
  окно Celery retry (макс 60с × 3 попытки + backoff).

At-most-once семантика: если XADD упал (Redis недоступен) — warning-лог,
без ретрая. Это UI-hint, не финансовая операция; в худшем случае юзер
увидит обновление через 30 с (mount-invalidate при следующем заходе)
или при ручном `refetch()`.

Стрим:    ``sse:user:{user_id}:{habit_id}`` (Redis Stream).
Retention: ``MAXLEN ~ 1000`` — приблизительное усечение последних ~1000
событий (O(1), не блокирует).

Поля XADD entry:
- event:       "checkin.accepted" | "checkin.rejected"
- habit_id:    uuid
- user_id:     numeric (НЕ PII — first_name/username не логируются)
- occurred_at: ISO 8601 UTC
- payload:     JSON (полный TodayResponse для accepted; {habit_id, reason, message} для rejected)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.core.logging import get_logger


@dataclass(slots=True, frozen=True)
class CheckinEvent:
    """Внутреннее представление события для XADD.

    Слой сервиса: готовится в `process_checkin._process` (там, где
    известны user_id, habit_id, outcome), передаётся в publisher.
    ``payload`` уже сериализуем в JSON (dict из pydantic ``.model_dump()``
    или hand-built dict).
    """

    event: str  # "checkin.accepted" | "checkin.rejected"
    payload: dict


class EventPublisher:
    """Асинхронная публикация событий в Redis Streams.

    DI: redis-клиент инжектится через конструктор (см. AGENTS.md — DI
    через конструктор, никакого глобального состояния). В проде — общий
    async-клиент, который уже создаёт ``_build_production_cache()`` в
    ``process_checkin``. В тестах — ``fakeredis.aioredis.FakeRedis()``.
    """

    STREAM_MAXLEN = 1000
    IDEMPOTENCY_KEY_TTL_SECONDS = 86400  # 24 часа

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._log = get_logger("worker.event_publisher")

    @staticmethod
    def stream_key(user_id: int, habit_id: str) -> str:
        """Ключ Redis Stream: per (user, habit)."""
        return f"sse:user:{user_id}:{habit_id}"

    @staticmethod
    def idempotency_key(membership_id: str, date_iso: str) -> str:
        """Ключ идемпотентности публикации: per (membership, date)."""
        return f"sse_published:checkin:{membership_id}:{date_iso}"

    async def publish_checkin(
        self,
        *,
        user_id: int,
        habit_id: str,
        membership_id: str,
        date_iso: str,
        event: CheckinEvent,
    ) -> bool:
        """Опубликовать событие чек-ина.

        Returns:
            True  — XADD выполнен (первая публикация для этой пары).
            False — пропущено по Guard 2 (повторная доставка) ИЛИ
                    XADD упал (at-most-once, потеря события).

        Note:
            Guard 1 (early-skip для ``already_exists``) делается в
            вызывающем коде (``process_checkin._process``), ДО вызова
            publish_checkin. Здесь — только Guard 2 + XADD.
        """
        idem_key = self.idempotency_key(membership_id, date_iso)
        # SET NX EX: True если ключ новый, None если уже есть.
        # redis-py 5.x: returns True/None (не True/False).
        acquired = await self._redis.set(
            idem_key, "1", nx=True, ex=self.IDEMPOTENCY_KEY_TTL_SECONDS
        )
        if not acquired:
            self._log.info(
                "sse_publish_skip_duplicate",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "membership_id": membership_id,
                    "date": date_iso,
                },
            )
            return False

        stream_key = self.stream_key(user_id, habit_id)
        fields = {
            "event": event.event,
            "habit_id": habit_id,
            "user_id": str(user_id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(event.payload, ensure_ascii=False, default=str),
        }
        try:
            await self._redis.xadd(
                stream_key,
                fields,
                maxlen=self.STREAM_MAXLEN,
                approximate=True,
            )
        except Exception as exc:  # noqa: BLE001 — at-most-once, см. док
            # Идемпотентность-ключ остался в Redis с TTL 24ч — повторная
            # доставка этой же пары в течение дня будет пропущена Guard 2.
            # Это приемлемо: SSE-событие — UI-hint, не финансовая операция.
            self._log.warning(
                "sse_publish_failed",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "err": str(exc),
                },
            )
            return False

        self._log.info(
            "sse_publish_ok",
            extra={
                "event": event.event,
                "user_id": user_id,
                "habit_id": habit_id,
            },
        )
        return True


__all__ = ["CheckinEvent", "EventPublisher"]

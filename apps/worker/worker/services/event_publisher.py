"""Worker-сервис публикации событий чек-ина в Redis Streams для SSE.

Шаг 3 плана `sse+redis.md` (редакция 5). Шаг 4 (backend XREAD) будет
потрельять из этих стримов — см. `apps/backend/app/services/sse/redis_stream_bus.py`.

Шаг 6 (Item 6, Pravki-deposit-sse.md §Z-6): добавлен `publish_to_habit` —
fan-out в ``sse:habit:{habit_id}`` для broadcast'а (catch_event).
Расширены `idempotency_key()` и `publish_checkin()` keyword-only
параметром `event_type` для namespace-изоляции (COLLISION-фикс из плана §Z-6.4).

Архитектурное место: worker-уровень (не backend), потому что:
- События публикуются СТРОГО после успешного commit в БД (task `process_checkin`).
- Если backend уронит commit — событие не должно уйти (Guard 1 в _process).
- Worker уже единственная точка синхронизации для дедупликации задачи.

Идемпотентность публикации (Guard 2, см. план §2.3 + §Z-6.4):
- Ключ персонального checkin:    ``sse_published:checkin:{membership_id}:{date}``.
- Ключ персонального you_were_caught: ``sse_published:caught:{membership_id}:{date}``.
- Ключ broadcast catch_event (новое):  ``sse_published:habit_catch:{penalty_id}``.
- Механика: ``SET key "1" NX EX 86400`` через тот же async redis-клиент.
- Если вернул ``None`` (ключ уже есть) — повторная Celery-доставка →
  XADD не выполняется, возвращается ``False``.
- TTL 24 часа покрывает полный день (с учётом TZ клубов, ±14 ч) плюс
  окно Celery retry (макс 60с × 3 попытки + backoff).

Backward-compat (важно): kwarg `event_type` имеет дефолт `"checkin"`,
поэтому существующие call-сайты `process_checkin._process` (строки 114, 152)
ПРОДОЛЖАЮТ писать байт-в-байт `sse_published:checkin:{m}:{d}` — ключ
НЕ переименовывается, существующие TTL-маркеры НЕ сбрасываются, миграция
не нужна.

At-most-once семантика: если XADD упал (Redis недоступен) — warning-лог,
без ретрая. Это UI-hint, не финансовая операция; в худшем случае юзер
увидит обновление через 30 с (mount-invalidate при следующем заходе)
или при ручном `refetch()`.

Стримы (Redis Stream, два namespace'а):
- Персональный: ``sse:user:{user_id}:{habit_id}``.
- Broadcast (новое): ``sse:habit:{habit_id}``.
- Клиент мультиплексирует оба в одном SSE-соединении (Item 7).
Retention: ``MAXLEN ~ 1000`` — приблизительное усечение последних ~1000
событий (O(1), не блокирует).

Поля XADD entry:
- event:       "checkin.accepted" | "checkin.rejected" | "you_were_caught" | "catch"
- habit_id:    uuid
- user_id:     numeric (НЕ PII — first_name/username не логируются)
- occurred_at: ISO 8601 UTC
- payload:     JSON (полный TodayResponse для accepted; {habit_id, reason, message} для rejected;
               {habit_id, catcher_user_id, violator_user_id, amount, penalty_id} для catch)
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
        """Ключ Redis Stream: per (user, habit) — персональный канал.

        Используется для checkin.accepted/rejected (юзер видит свой
        чек-ин) и для you_were_caught (жертва видит свой статус).
        """
        return f"sse:user:{user_id}:{habit_id}"

    @staticmethod
    def habit_stream_key(habit_id: str) -> str:
        """Ключ Redis Stream: per habit — broadcast-канал.

        Pravki-deposit-sse.md §Z-6.2: один XADD в ``sse:habit:{habit_id}``
        — клиент мультиплексирует user-strim + habit-strim в одном
        XREAD (Item 7). Без SQL-запроса за списком участников — это
        НЕ fan-out с backend'а. Фронт сам фильтрует события по membership_id.
        """
        return f"sse:habit:{habit_id}"

    @staticmethod
    def idempotency_key(
        membership_id: str,
        date_iso: str,
        *,
        event_type: str = "checkin",
    ) -> str:
        """Ключ идемпотентности публикации: per (event_type, scope).

        Backward-compat: дефолт ``event_type="checkin"`` сохраняет
        байт-в-байт старый формат ``sse_published:checkin:{m}:{d}`` для
        существующих call-сайтов в ``process_checkin._process``.
        Для ``you_were_caught`` call-сайт передаёт ``event_type="caught"``
        → ``sse_published:caught:{m}:{d}`` — независимый namespace, коллизий
        с checkin нет.

        Namespace-изоляция (Pravki §Z-6.4 COLLISION-фикс): утренний
        ``checkin.rejected`` (status='missed'/'joined_late') забивал
        старый ключ ``sse_published:checkin:{m}:{d}`` на 24ч, и вечерний
        ``you_were_caught`` для той же (m, d) терялся (SET NX → False).
        С новым kwarg этого не происходит.
        """
        return f"sse_published:{event_type}:{membership_id}:{date_iso}"

    @staticmethod
    def habit_idempotency_key(
        *,
        event_type: str,
        unique_id: str,
    ) -> str:
        """Ключ идемпотентности для habit-broadcast (Item 6).

        Формат: ``sse_published:{event_type}:{unique_id}``. Где unique_id —
        обычно ``penalty_id`` (UUID Penalty row, уникален).

        Пример: ``sse_published:habit_catch:abc-123-def-456``.

        Namespace изолирован от персональных ключей (другой scope —
        ``unique_id``, а не ``{membership_id}:{date_iso}``), поэтому
        коллизий с idempotency_key() нет.
        """
        return f"sse_published:{event_type}:{unique_id}"

    async def publish_checkin(
        self,
        *,
        user_id: int,
        habit_id: str,
        membership_id: str,
        date_iso: str,
        event: CheckinEvent,
        event_type: str = "checkin",  # Pravki §Z-6.4: дефолт = байт-в-байт совместимо
    ) -> bool:
        """Опубликовать событие чек-ина.

        Returns:
            True  — XADD выполнен (первая публикация для этой пары).
            False — пропущено по Guard 2 (повторная доставка) ИЛИ
                    Redis недоступен на любой стадии (SET NX / XADD).
                    At-most-once: чек-ин уже в БД, событие — UI-hint.

        Note:
            Guard 1 (early-skip для ``already_exists``) делается в
            вызывающем коде (``process_checkin._process``), ДО вызова
            publish_checkin. Здесь — только Guard 2 + XADD под единой
            защитой try/except, чтобы сетевой блип Redis на ЛЮБОЙ
            стадии не пробросил исключение в уже закоммиченный task.
        """
        # Pravki §Z-6.4 COLLISION-фикс: event_type kwarg с дефолтом
        # "checkin" сохраняет byte-for-byte старый формат ключа
        # ``sse_published:checkin:{m}:{d}`` для существующих call-сайтов.
        idem_key = self.idempotency_key(
            membership_id, date_iso, event_type=event_type,
        )
        stream_key = self.stream_key(user_id, habit_id)
        fields = {
            "event": event.event,
            "habit_id": habit_id,
            "user_id": str(user_id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(event.payload, ensure_ascii=False, default=str),
        }
        try:
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
                        "event_type": event_type,
                    },
                )
                return False
            await self._redis.xadd(
                stream_key,
                fields,
                maxlen=self.STREAM_MAXLEN,
                approximate=True,
            )
        except Exception as exc:  # noqa: BLE001 — at-most-once, см. док
            # Покрывает ОБЕ стадии:
            # 1. SET NX упал (ConnectionError / timeout) — идемпотентность-ключ
            #    НЕ выставлен. При Celery retry той же задачи process_checkin
            #    вернёт duplicate=True (CheckinAlreadyExistsError) и Guard 1
            #    в _process пропустит публикацию. Событие потеряно — ок для MVP.
            # 2. XADD упал (Redis умер между операциями) — идемпотентность-ключ
            #    УЖЕ в Redis с TTL 24ч. Повторная доставка будет пропущена
            #    Guard 2. Тоже ок: at-most-once для UI-hint.
            # В обоих случаях чек-ин в БД остаётся — публикация не должна
            # ломать уже закоммиченный task.
            self._log.warning(
                "sse_publish_failed",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "membership_id": membership_id,
                    "event_type": event_type,
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
                "event_type": event_type,
            },
        )
        return True

    async def publish_to_habit(
        self,
        *,
        habit_id: str,
        event_type: str,
        payload: dict,
        scope_suffix: str,
    ) -> bool:
        """Fan-out: один XADD в habit-стрим для broadcast'а (Item 6, Pravki §Z-6).

        Используется для catch_event: после apply_catch backend handler
        отправляет celery-таску publish_catch_event → worker вызывает
        этот метод → один XADD в ``sse:habit:{habit_id}`` — все
        активные SSE-клиенты этого клуба получают событие через multiplex
        (Item 7).

        Идемпотентность: ключ ``sse_published:{event_type}:{unique_id}`` где
        ``unique_id=scope_suffix``. Для catch_event scope_suffix — это
        ``penalty.id`` (UUID, уникален). Retry celery-таски (например,
        broker reconnect) приведёт к duplicate=True → XADD не выполнится.
        TTL 24 часа — перекрывает окно retry + возможные stragglers.

        Args:
            habit_id: UUID клуба.
            event_type: namespace для idempotency_key. Пример: ``"habit_catch"``.
                НЕ путать с ``event.event`` (поле внутри XADD entry —
                например ``"catch"``), это разные вещи: event_type
                определяет namespace ключа, event — содержимое стрима.
            payload: dict, сериализуется в JSON в поле ``payload`` XADD entry.
            scope_suffix: уникальный id для идемпотентности (penalty_id,
                checkin_id, transaction_id и т.п.).

        Returns:
            True  — XADD выполнен (первая публикация для этого scope_suffix).
            False — пропущено по Guard 2 (повторная доставка) ИЛИ
                    Redis недоступен.
        """
        stream_key = self.habit_stream_key(habit_id)
        idem_key = self.habit_idempotency_key(
            event_type=event_type, unique_id=scope_suffix,
        )
        fields = {
            "event": payload.get("event", event_type),  # inner XADD event name
            "habit_id": habit_id,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
        }
        try:
            acquired = await self._redis.set(
                idem_key, "1", nx=True, ex=self.IDEMPOTENCY_KEY_TTL_SECONDS,
            )
            if not acquired:
                self._log.info(
                    "sse_publish_habit_skip_duplicate",
                    extra={
                        "habit_id": habit_id,
                        "event_type": event_type,
                        "scope_suffix": scope_suffix,
                    },
                )
                return False
            await self._redis.xadd(
                stream_key,
                fields,
                maxlen=self.STREAM_MAXLEN,
                approximate=True,
            )
        except Exception as exc:  # noqa: BLE001 — at-most-once
            self._log.warning(
                "sse_publish_habit_failed",
                extra={
                    "habit_id": habit_id,
                    "event_type": event_type,
                    "scope_suffix": scope_suffix,
                    "err": str(exc),
                },
            )
            return False

        self._log.info(
            "sse_publish_habit_ok",
            extra={
                "habit_id": habit_id,
                "event_type": event_type,
                "scope_suffix": scope_suffix,
            },
        )
        return True


__all__ = ["CheckinEvent", "EventPublisher"]

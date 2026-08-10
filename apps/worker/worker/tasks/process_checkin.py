from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from app.core.constants import ProofType
from app.core.exceptions import (
    CheckinAlreadyCaughtError,
    CheckinAlreadyExistsError,
    CheckinJoinedLateError,
    CheckinWindowClosedError,
    CheckinWrongTopicError,
    MembershipNotActiveError,
    MembershipNotFoundError,
)
from app.core.logging import get_logger
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.penalty_repository import PenaltyRepository
from app.schemas import CheckinStatusOut, HabitOut, MembershipOut, TodayResponse
from app.services.checkin_service import CheckinService
from app.services.proof_validator import ProofMessage, ProofValidationError
from db.session import async_session_factory  # type: ignore[import-not-found]
from worker.services.event_publisher import CheckinEvent, EventPublisher


class CachePort(Protocol):
    """Тот же протокол, что в CheckinService — единый контракт между слоями."""

    async def invalidate_today(self, habit_id: str, membership_id: str) -> None: ...


async def _build_today_payload(
    *,
    user_id: int,
    habit_id: str,
    session_factory,
) -> dict | None:
    """Собирает полный TodayResponse (как GET /habits/{id}/today).

    Использует ОТДЕЛЬНУЮ сессию — основная к моменту вызова уже
    закоммичена и release'нута. Это +1 DB-сессия, но все запросы
    indexed; в прод-трафике сотни чек-инов/мин * 3 запроса = десятки
    RPS, БД держит.

    Returns None при MembershipNotFound / HabitArchived — для Guard 1
    это сигнал "не публикуем, UI уже знает".
    """
    from app.core.exceptions import HabitArchivedError, MembershipNotFoundError

    async with session_factory() as session:
        service = CheckinService(
            session=session,
            habit_repo=HabitRepository(session),
            membership_repo=MembershipRepository(session),
            checkin_repo=CheckinRepository(session),
            penalty_repo=PenaltyRepository(session),
            cache=None,
        )
        try:
            habit, membership, stats = await service.get_today_status(
                user_id=user_id,
                habit_id=habit_id,
                now_utc=datetime.now(tz=timezone.utc),
            )
        except (HabitArchivedError, MembershipNotFoundError):
            return None
        return TodayResponse(
            habit=HabitOut(
                id=str(habit.id),
                title=habit.title,
                description=habit.description,
                chat_id=habit.chat_id,
                checkin_window_start=habit.checkin_window_start.isoformat(),
                checkin_window_end=habit.checkin_window_end.isoformat(),
                timezone=habit.timezone,
                penalty_amount=habit.penalty_amount,
                price_month=habit.price_month,
                proof_type=habit.proof_type.value,
                proof_types=list(habit.proof_types),
                prize_pool=habit.prize_pool,
                members_count=0,
                is_active=habit.is_active,
                photo_url=habit.photo_url,
                telegram_invite_link=habit.telegram_invite_link,
                checkin_topic_thread_id=habit.checkin_topic_thread_id,
                chat_topic_thread_id=habit.chat_topic_thread_id,
            ),
            membership=MembershipOut.model_validate(membership),
            checkin=CheckinStatusOut(
                status=stats.status,
                checkin_count=stats.checkin_count,
                streak_days=stats.streak_days,
                penalties_count=stats.penalties_count,
                penalties_total=stats.penalties_total,
                deadline_at=None,
            ),
        ).model_dump(mode="json")


async def _publish_checkin_accepted(
    publisher: EventPublisher | None,
    *,
    user_id: int,
    habit_id: str,
    membership_id: str,
    date_iso: str,
    today_payload: dict,
) -> None:
    """Шаг 3: XADD checkin.accepted с полным TodayResponse."""
    if publisher is None:
        return
    await publisher.publish_checkin(
        user_id=user_id,
        habit_id=habit_id,
        membership_id=membership_id,
        date_iso=date_iso,
        event=CheckinEvent(event="checkin.accepted", payload=today_payload),
    )


async def _publish_checkin_rejected(
    publisher: EventPublisher | None,
    *,
    session_factory,
    user_id: int,
    habit_id: str,
    reason: str,
    message: str,
) -> None:
    """Шаг 3: XADD checkin.rejected. Резолвит membership_id и date_iso
    из БД через ОТДЕЛЬНУЮ сессию (основная уже rollback'нута).

    Skip при MembershipNotFound — нет membership_id, идемпотентность
    не выстроить, и в UI ничего рисовать нечего (пользователь не в клубе).
    """
    if publisher is None:
        return

    async def _inner() -> None:
        async with session_factory() as session:
            habit = await HabitRepository(session).get(habit_id)
            if habit is None:
                return
            membership = await MembershipRepository(session).get_for_user_in_habit(
                user_id, habit_id
            )
            if membership is None:
                return
            club_date = habit.club_date(datetime.now(tz=timezone.utc))
            await publisher.publish_checkin(
                user_id=user_id,
                habit_id=habit_id,
                membership_id=str(membership.id),
                date_iso=club_date.isoformat(),
                event=CheckinEvent(
                    event="checkin.rejected",
                    payload={
                        "habit_id": habit_id,
                        "reason": reason,
                        "message": message,
                    },
                ),
            )

    try:
        await _inner()
    except Exception as exc:  # noqa: BLE001 — публикация не должна ронять задачу
        get_logger("worker.checkin").warning(
            "sse_publish_rejected_failed",
            extra={"user_id": user_id, "habit_id": habit_id, "err": str(exc)},
        )


async def _process(
    payload: dict,
    *,
    cache: CachePort | None = None,
    publisher: EventPublisher | None = None,
    session_factory=None,
) -> dict:
    """Чистая async-функция для тестов и для Celery-обёртки.

    Идемпотентность обеспечивается UNIQUE-индексом
    `uq_checkins_membership_date (membership_id, date)` в БД.

    Транзакция: одна на всю таску. Service.flush() пишет строку, затем commit().
    При IntegrityError (дубль чек-ина) — rollback + идемпотентный ok-ответ.

    SSE-публикация (Шаг 3 плана sse+redis.md):
    - Guard 1 (early-skip): ``result["duplicate"] is True`` — никаких Redis-операций.
      Это дубль чек-ина (либо повторная Celery-доставка, либо два видео-кружка
      подряд от юзера). UI уже показывает ``done``, событие бесполезно.
    - Guard 2 (idempotency): внутри ``EventPublisher.publish_checkin`` через
      ``SET NX EX``. Защита от двойного XADD при Celery redelivery.
    - At-most-once: XADD-фейл не ретраит задачу (UI-hint, не финансовая операция).

    DI: cache (опциональный), publisher (опциональный) и session_factory
    (опциональный) передаются снаружи. Это позволяет тестам не поднимать
    ни Redis, ни Postgres/SQLite — достаточно передать свою фабрику
    сессий и mock publisher. Прод-обёртка ниже создаёт настоящие
    Redis-клиент (для cache + publisher) и фабрику по умолчанию.
    """
    log = get_logger("worker.checkin")
    from sqlalchemy.exc import IntegrityError

    factory = session_factory if session_factory is not None else async_session_factory

    async with factory() as session:
        try:
            proof = ProofMessage(
                proof_type=ProofType(payload["proof_type"]),
                text=payload.get("text"),
                video_note_duration=payload.get("duration_seconds"),
                photo_sizes=1 if payload["proof_type"] == "photo" else 0,
                message_date=datetime.fromisoformat(payload["message_sent_at"]),
            )
            service = CheckinService(
                session=session,
                habit_repo=HabitRepository(session),
                membership_repo=MembershipRepository(session),
                checkin_repo=CheckinRepository(session),
                penalty_repo=PenaltyRepository(session),
                cache=cache,
            )
            checkin, created = await service.process_checkin(
                user_id=payload["user_id"],
                habit_id=payload["habit_id"],
                proof=proof,
                proof_message_id=payload["message_id"],
                now_utc=datetime.now(tz=timezone.utc),
                message_thread_id=payload.get("message_thread_id"),
            )
            await session.commit()
            log.info(
                "worker_checkin_ok",
                extra={
                    "checkin_id": str(checkin.id),
                    "created": created,
                    "user_id": payload["user_id"],
                    "habit_id": payload["habit_id"],
                },
            )
            membership_id = str(checkin.membership_id)
            # Снимаем club_date СРАЗУ после commit, пока ORM-объект живой
            # (после закрытия async with доступ к detached-объекту fragile).
            date_iso = checkin.date.isoformat()
            result = {
                "ok": True,
                "checkin_id": str(checkin.id),
                "created": created,
                "duplicate": not created,
            }
        except CheckinAlreadyExistsError:
            await session.rollback()
            log.info("worker_checkin_duplicate", extra={"user_id": payload["user_id"]})
            return {"ok": True, "duplicate": True}
        except (
            ProofValidationError,
            CheckinWindowClosedError,
            CheckinJoinedLateError,    # Pravki-bug-fixes §Z-19
            CheckinAlreadyCaughtError, # Pravki-bug-fixes §Z-21 (Item 4)
            CheckinWrongTopicError,
            MembershipNotActiveError,
            MembershipNotFoundError,
        ) as exc:
            await session.rollback()
            reason_code = getattr(exc, "code", "rejected")
            reason_message = getattr(exc, "message", reason_code)
            log.warning(
                "worker_checkin_rejected",
                extra={"code": reason_code, "err": str(exc)},
            )
            # Publish checkin.rejected (Шаг 3). MembershipNotFoundError
            # отфильтруется внутри — нет membership_id, нечего дедупить.
            await _publish_checkin_rejected(
                publisher,
                session_factory=factory,
                user_id=payload["user_id"],
                habit_id=payload["habit_id"],
                reason=reason_code,
                message=reason_message,
            )
            # Pravki-bug-fixes §Z-19: для joined_late возвращаем window_start/end
            # чтобы бот в race-fallback мог показать дружественное сообщение
            # с временем окна. Habit получаем через ОТДЕЛЬНУЮ сессию (основная
            # уже rollback'нута). Если habit не найден — fallback на пустые строки.
            result: dict = {"ok": False, "code": reason_code, "message": reason_message}
            if reason_code == "joined_late":
                try:
                    async with factory() as race_session:
                        race_habit = await HabitRepository(race_session).get(
                            payload["habit_id"]
                        )
                    if race_habit is not None:
                        result["window_start"] = race_habit.checkin_window_start.strftime("%H:%M")
                        result["window_end"] = race_habit.checkin_window_end.strftime("%H:%M")
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "worker_joined_late_race_window_fetch_failed",
                        extra={"err": str(exc), "kind": exc.__class__.__name__},
                    )
            return result
        except IntegrityError as exc:
            await session.rollback()
            log.warning("worker_checkin_integrity", extra={"err": str(exc)})
            return {"ok": True, "duplicate": True}
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            log.error("worker_checkin_failed", extra={"err": str(exc)})
            return {"ok": False, "err": str(exc)}

    # === Шаг 3: SSE publish ===
    # Guard 1: дубль чек-ина → никаких Redis-операций.
    if result["duplicate"]:
        return result

    # Только created=True → публикуем checkin.accepted с полным TodayResponse.
    # payload строится в ОТДЕЛЬНОЙ сессии (основная уже закрыта).
    today_payload = await _build_today_payload(
        user_id=payload["user_id"],
        habit_id=payload["habit_id"],
        session_factory=factory,
    )
    if today_payload is None:
        log.warning(
            "sse_publish_skip_missing_state",
            extra={"user_id": payload["user_id"], "habit_id": payload["habit_id"]},
        )
        return result

    await _publish_checkin_accepted(
        publisher,
        user_id=payload["user_id"],
        habit_id=payload["habit_id"],
        membership_id=membership_id,
        date_iso=date_iso,
        today_payload=today_payload,
    )
    return result


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore


def _build_production_cache() -> CachePort | None:
    """Создаёт production-Redis-клиент. Lazy import чтобы не тянуть redis
    при юнит-тестах."""
    import os

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    import redis.asyncio as aioredis

    from app.services.today_cache import RedisTodayCache

    return RedisTodayCache(aioredis.from_url(redis_url, decode_responses=True))


def _build_production_publisher() -> EventPublisher | None:
    """Создаёт production EventPublisher. Тот же Redis-клиент, что и cache
    (DB 0, namespace разделены ключами). None если REDIS_URL не задан."""
    import os

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    import redis.asyncio as aioredis

    return EventPublisher(aioredis.from_url(redis_url, decode_responses=True))


if celery_app is not None:

    @celery_app.task(
        name="worker.tasks.process_checkin.run",
        bind=True,
        max_retries=3,
        autoretry_for=(Exception,),
        dont_autoretry_for=(
            CheckinAlreadyExistsError,
            CheckinJoinedLateError,    # Pravki-bug-fixes §Z-19: не ретраить — итог детерминирован.
            CheckinAlreadyCaughtError, # Pravki-bug-fixes §Z-21 (Item 4): то же самое — deterministic.
            ProofValidationError,
            CheckinWindowClosedError,
            CheckinWrongTopicError,
            MembershipNotActiveError,
            MembershipNotFoundError,
        ),
        retry_backoff=True,
        retry_backoff_max=60,
        retry_jitter=True,
    )
    def run(self, payload: dict) -> dict:  # type: ignore[no-redef]
        import asyncio

        cache = _build_production_cache()
        publisher = _build_production_publisher()
        return asyncio.run(
            _process(
                payload,
                cache=cache,
                publisher=publisher,
                session_factory=async_session_factory,
            )
        )
else:
    run = _process

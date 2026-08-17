from __future__ import annotations

import os
from datetime import datetime, timezone

from app.core.constants import MembershipStatus
from app.core.logging import get_logger
from app.models.habit import Habit
from app.models.membership import Membership
from app.models.user import User
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.services.penalty_service import PenaltyService


async def _close_for_habit(session, habit: Habit, now_utc: datetime) -> dict:
    """Штрафует участников без чек-ина за club_date(now).

    Защита от раннего срабатывания: если окно чек-ина в TZ клуба ещё не закрылось,
    пропускаем (сегодня ещё не пропущено — рано штрафовать).

    Стриминг: memberships подгружаются через `iter_for_habit` (server-side
    cursor, asyncpg). Память O(1) на итерацию, штрафы применяются к каждому
    member'у по мере получения. Идемпотентность — на стороне PenaltyService
    (INSERT ON CONFLICT DO NOTHING по (membership_id, date)).
    """
    if habit.is_within_checkin_window(now_utc):
        return {
            "habit_id": str(habit.id),
            "skipped": "window_open",
            "penalized": 0,
        }

    club_date = habit.club_date(now_utc)
    membership_repo = MembershipRepository(session)
    habit_repo = HabitRepository(session)
    checkin_repo = __import__(
        "app.repositories.checkin_repository", fromlist=["CheckinRepository"]
    ).CheckinRepository(session)
    from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository

    penalty_service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        suspicious_repo=SuspiciousPairsRepository(session),
    )

    penalized = 0
    waived_count = 0
    notifications: list[tuple[Membership, int]] = []
    async for membership in membership_repo.iter_for_habit(str(habit.id)):
        # Общие фильтры (одинаковы для ACTIVE и PAUSED).
        existing = await checkin_repo.get_for_date(str(membership.id), club_date)
        if existing is not None:
            continue
        # 7.3: новый участник, вступивший в club_date, не считается
        # пропавшим — пропуск начинается со следующего клуб-дня.
        # joined_at NOT NULL в schema, default = now() — значит
        # None здесь невозможен в проде; defensive check избыточен.
        if membership.joined_at.date() >= club_date:
            continue
        # Ветвление по статусу.
        # Pravki-no-deposit-waived-marker (коммит A 2026-08-17):
        # PAUSED юзер (deposit < penalty через recompute_pause_status) не может
        # платить — помечаем день как «уже разрешённый» через WAIVED-маркер,
        # чтобы apply_catch после topup не списал деньги повторно за тот день.
        # ACTIVE идёт через apply_window_expired (списание штрафа или редкий
        # ACTIVE+deposit=0 → WAIVED). LEFT skip'ается явно.
        if membership.status == MembershipStatus.ACTIVE:
            penalty = await penalty_service.apply_window_expired(
                violator_membership_id=str(membership.id),
                club_date=club_date,
            )
            if penalty is not None:
                penalized += 1
                notifications.append(
                    (membership, int(penalty.amount))
                )
        elif membership.status == MembershipStatus.PAUSED:
            marker = await penalty_service.mark_waived_unable_to_pay(
                violator_membership_id=str(membership.id),
                club_date=club_date,
            )
            if marker is not None:
                waived_count += 1

    return {
        "habit_id": str(habit.id),
        "penalized": penalized,
        "waived": waived_count,
        "notifications": notifications,
    }


async def _publish_window_closed_notifications(
    *,
    habit: Habit,
    notifications: list[tuple[Membership, int]],
    bot_token: str,
) -> None:
    if not bot_token or habit.chat_id == 0 or not notifications:
        return
    from app.services.notification_service import NotificationService

    from db.session import async_session_factory  # type: ignore[import-not-found]

    async with async_session_factory() as session:  # type: ignore[name-defined]
        for violator_membership, amount in notifications:
            violator_user = await session.get(
                User, int(violator_membership.user_id)
            )
            service = NotificationService(bot_token=bot_token)
            await service.notify_window_closed(
                habit=habit,
                violator_membership=violator_membership,
                violator_user=violator_user,
                penalty_amount_kopecks=amount,
            )


async def _process() -> dict:
    log = get_logger("worker.close_catch_window")
    from db.session import async_session_factory  # type: ignore[import-not-found]

    summary: list[dict] = []
    habits_for_notification: list[tuple[Habit, list[tuple[Membership, int]]]] = []
    async with async_session_factory() as session:  # type: ignore[name-defined]
        habit_repo = HabitRepository(session)
        now_utc = datetime.now(tz=timezone.utc)
        # Стриминг клубов через `iter_active` — ORM тащит строки по мере
        # обработки, не загружая 100+ клубов целиком в память.
        async for habit in habit_repo.iter_active():
            result = await _close_for_habit(session, habit, now_utc)
            notif_list = result.pop("notifications", [])
            summary.append(result)
            if notif_list:
                habits_for_notification.append((habit, notif_list))
        await session.commit()

    bot_token = os.getenv("BOT_TOKEN", "")
    if bot_token:
        for habit, notifications in habits_for_notification:
            try:
                await _publish_window_closed_notifications(
                    habit=habit,
                    notifications=notifications,
                    bot_token=bot_token,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "close_catch_window.notification_failed",
                    extra={
                        "habit_id": str(habit.id),
                        "err": str(exc),
                    },
                )

    log.info("close_catch_window_done", extra={"summary": summary})
    return {"summary": summary}


try:
    from worker.celery_app import celery_app
except ImportError:
    celery_app = None  # type: ignore

if celery_app is not None:

    @celery_app.task(name="worker.tasks.close_catch_window.run_for_active_habits")
    def run_for_active_habits() -> dict:
        import asyncio

        return asyncio.run(_process())
else:
    run_for_active_habits = _process
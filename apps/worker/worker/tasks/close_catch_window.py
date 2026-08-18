"""Worker-таска `close_catch_window` — housekeeping после закрытия catch window.

Pravki-manual-catch-2026-08-18 §Шаг 3 (Commit 2): переписана. Авто-списание
отключено. Штраф возможен ТОЛЬКО через ручную поимку (`apply_catch`).

Что делает cron (housekeeping после закрытия catch window «вчера»):
1. **Gate на now_utc:** пропускаем если check-in window клуба ещё открыт.
2. **Gate на catch_window_end:** для `housekeeping_club_date = yesterday_in_club_tz`
   проверяем `now_utc > catch_window_end(housekeeping_club_date)`. Если не —
   skip («вчерашний» catch window ещё не закрылся).
3. После gate для каждого не-LEFT члена без чек-ина за `housekeeping_club_date`:
   - `upsert Checkin(status='missed')` — для истории/UI (не для финансов).
   - Под `lock_for_update(user)` вызов `recompute_pause_status` — sync
     статуса с депозитом. Без денежных движений; если
     `deposit < penalty` → `PAUSED`.

**Почему «вчера», а не «сегодня»:** `habit.club_date(now_utc)` возвращает
дату «сейчас» в TZ клуба. Catch window для club_date D заканчивается в
D+1 (например, для 09:00-21:00 MSK catch window 18 aug кончается в
04:00 UTC 19 aug). Когда мы в 04:05 UTC 19 aug, club_date в MSK = 19 aug,
но catch window, который только что закрылся — для 18 aug. Поэтому
housekeeping работает с `club_date = club_date(now_utc - 1 day)`.

Что НЕ делает (Commit 2):
- Не списывает деньги, не создаёт `Penalty` / `Transaction`.
- Не отправляет уведомления о списании (текст был ложным после отключения
  авто-списания; удалено целиком `_publish_window_closed_notifications`).

Идемпотентность: повторный запуск → `upsert_status` перезаписывает
MISSED → MISSED (no-op), `recompute_pause_status` вычисляет одинаковый
статус (no-op). Никаких side-effects.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.constants import CheckinStatus, MembershipStatus
from app.core.logging import get_logger
from app.models.habit import Habit
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository
from app.services.membership_service import MembershipService


async def _close_for_habit(session, habit: Habit, now_utc: datetime) -> dict:
    """Housekeeping для одного клуба. Возвращает summary-запись.

    Args:
        session: async-сессия (worker transaction; caller делает commit).
        habit: клуб для обработки.
        now_utc: момент запуска (tz-aware UTC datetime).

    Returns:
        dict для summary: {habit_id, skipped?, marked_missed, club_date}.
    """
    if not now_utc.tzinfo:
        raise ValueError(
            "_close_for_habit requires tz-aware UTC datetime; "
            "worker captures captures_now_utc via datetime.now(tz=timezone.utc)."
        )

    # Housekeeping работает за «вчера» в TZ клуба: catch window вчерашнего
    # club_date только что закрылся. club_date(now - 1 day) даёт
    # корректную дату с учётом TZ (для constant-offset TZ типа MSK/JST
    # это эквивалентно «сегодня минус 1 день в TZ клуба»).
    housekeeping_club_date = habit.club_date(now_utc - timedelta(days=1))

    # Gate: catch window для housekeeping_club_date ещё не закрылся → skip.
    # Строгое `>` против catch_window_end: на границе (последняя секунда)
    # ещё НЕ housekeeping. is_within_catch_window inclusive с обеих сторон,
    # gate — нет, чтобы исключить race на границе.
    catch_end_utc = habit.catch_window_end(housekeeping_club_date)
    if not now_utc > catch_end_utc:
        return {
            "habit_id": str(habit.id),
            "skipped": "catch_window_open",
            "marked_missed": 0,
            "club_date": str(housekeeping_club_date),
        }

    # Housekeeping: для каждого не-LEFT члена без чек-ина за
    # housekeeping_club_date.
    membership_repo = MembershipRepository(session)
    habit_repo = HabitRepository(session)
    checkin_repo = CheckinRepository(session)
    user_repo = UserRepository(session)
    membership_service = MembershipService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        user_repo=user_repo,
    )

    marked_missed = 0
    async for membership in membership_repo.iter_for_habit(str(habit.id)):
        existing = await checkin_repo.get_for_date(
            str(membership.id), housekeeping_club_date
        )
        if existing is not None:
            # Уже есть Checkin (юзер отметился, был пойман ранее, или
            # cron уже отработал). Idempotency: пропускаем.
            continue
        # PR §7.3: новичок сегодня (joined_at >= housekeeping_club_date) →
        # пропускаем. For housekeeping_club_date=2026-08-18: новичок 18 aug
        # не считается пропавшим (joined_at.date()=2026-08-18 >= club_date).
        if membership.joined_at.date() >= housekeeping_club_date:
            continue
        if membership.status == MembershipStatus.LEFT:
            # Не трогаем LEFT: явное действие юзера, не автопауза.
            continue

        # user-lock обязателен для recompute_pause_status (Pravki Z-2.4):
        # sync статуса без lock'а может записать устаревший статус поверх
        # параллельного top-up / smart renew / catch (которые тоже лочат user).
        await user_repo.lock_for_update(membership.user_id)

        # 1. История/UI — Checkin.missed. Без финансовых последствий.
        #    ON CONFLICT DO UPDATE: idempotency.
        await checkin_repo.upsert_status(
            membership_id=str(membership.id),
            on_date=housekeeping_club_date,
            status=CheckinStatus.MISSED,
        )

        # 2. Sync статуса с депозитом: deposit<penalty → PAUSED.
        #    Никаких денежных движений; функция уже реализована для
        #    других triggers (apply_catch, top-up, subscribe_and_join).
        await membership_service.recompute_pause_status(membership.user_id)

        marked_missed += 1

    return {
        "habit_id": str(habit.id),
        "marked_missed": marked_missed,
        "club_date": str(housekeeping_club_date),
    }


async def _process() -> dict:
    log = get_logger("worker.close_catch_window")
    from db.session import async_session_factory  # type: ignore[import-not-found]

    summary: list[dict] = []
    async with async_session_factory() as session:  # type: ignore[name-defined]
        habit_repo = HabitRepository(session)
        # tz-aware UTC: catch_window_end и recompute_pause_status требуют aware.
        now_utc = datetime.now(tz=timezone.utc)
        # Стриминг клубов через `iter_active` — ORM тащит строки по мере
        # обработки, не загружая 100+ клубов целиком в память.
        async for habit in habit_repo.iter_active():
            result = await _close_for_habit(session, habit, now_utc)
            summary.append(result)
        await session.commit()

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
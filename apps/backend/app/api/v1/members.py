from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.users import TelegramUserDbDep
from app.core.constants import CheckinStatus
from app.core.deps import RedisDep, SessionDep
from app.core.exceptions import HabitArchivedError, PenaltyAlreadyProcessedError
from app.core.logging import get_logger
from app.models.checkin import Checkin
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.penalty_repository import PenaltyRepository
from app.repositories.suspicious_pairs_repository import SuspiciousPairsRepository
from app.services.catch_rate_limiter import RedisCatchRateLimiter
from app.services.penalty_service import PenaltyService

router = APIRouter()


class MemberRowOut(BaseModel):
    """Строка члена клуба в списке участников.

    checkin_count — общее число done-чекинов за всё время
    (Pravki.md 2026-07-24: "сколько отчекинился, столько и на счетчике").
    Раньше был `streak_days=0` (заглушка).

    photo_url — relative путь /api/v1/users/{id}/photo (Pravki §7.1 v3.1).
    NULL = нет аватарки или worker не подтянул. Frontend оборачивает
    в new URL(photo_url, window.location.origin).

    membership_status: Pravki-paused-frontend-2026-08-14. Отдельное поле от
    status (CheckinStatus) — описывает состояние ЧЛЕНСТВА, а не чек-ина.
    Используется фронтом для UX-фильтра в MembersPage: paused-юзеры
    (депозит=0) не показываются в секции «Кого можно поймать» —
    с них всё равно нечего взять. Защита от race-condition с catch
    остаётся на бэкенде (MembershipNotActiveError в apply_catch +
    re-check после user-lock от коммита 3 сегодняшней серии).
    """

    membership_id: str
    user_id: int
    first_name: str
    username: str | None
    status: str
    checkin_count: int
    can_catch: bool
    membership_status: str = "active"  # 'active' | 'paused' | 'left' (defensive)
    photo_url: str | None = None


class MembersResponse(BaseModel):
    items: list[MemberRowOut]


class CatchRequest(BaseModel):
    violator_membership_id: str


class CatchResponse(BaseModel):
    ok: bool
    code: str | None = None
    amount: int | None = None


@router.get("/habits/{habit_id}/members", response_model=MembersResponse)
async def list_members(
    habit_id: str,
    user: TelegramUserDbDep,
    session: SessionDep,
) -> MembersResponse:
    habit_repo = HabitRepository(session)
    membership_repo = MembershipRepository(session)
    checkin_repo = CheckinRepository(session)
    penalty_repo = PenaltyRepository(session)

    habit = await habit_repo.get(habit_id)
    if habit is None or habit.archived_at is not None:
        raise HabitArchivedError()

    memberships = await membership_repo.list_for_habit(habit_id)
    club_date = habit.club_date(datetime.now(tz=UTC))

    # Хак: достаём user.first_name из БД. В шаге 6 добавим UserRepository.
    user_id_to_name = await _user_names(session, [m.user_id for m in memberships])
    # photo_file_id для аватарок (Pravki §7.1 v3.1).
    user_id_to_photo = await _user_photo_file_ids(
        session, [m.user_id for m in memberships]
    )

    # Одним запросом достаём COUNT(done) GROUP BY membership_id для всех
    # членов клуба. Раньше возвращалось streak_days=0 (заглушка).
    member_ids = [str(m.id) for m in memberships]
    counts: dict[str, int] = {}
    if member_ids:
        rows = (
            await session.execute(
                select(Checkin.membership_id, func.count(Checkin.id))
                .where(
                    Checkin.membership_id.in_(member_ids),
                    Checkin.status == CheckinStatus.DONE,
                )
                .group_by(Checkin.membership_id)
            )
        ).all()
        counts = {str(m_id): int(c) for m_id, c in rows}

    # Pravki-bug-fixes §Z-21 (can_catch fix): если у юзера есть ЛЮБОЙ Penalty
    # за club_date (caught ИЛИ window_closed_no_catch) — повторный catch даст
    # amount=0 / penalty_already_processed, поэтому can_catch=False.
    # Один batch-запрос по всем членам клуба (по аналогии с counts).
    penalty_set: set[str] = await penalty_repo.ids_with_any_penalty_today(
        membership_ids=member_ids,
        club_date=club_date,
    )

    now = datetime.now(tz=UTC)
    members: list[MemberRowOut] = []
    for m in memberships:
        existing = await checkin_repo.get_for_date(str(m.id), club_date)
        if existing is not None:
            status = existing.status.value
        else:
            # Pravki-bug-fixes §Z-19 (joiner-late protection):
            # если Checkin на сегодня нет, определяем статус исходя из
            # (а) membership.joined_at в TZ клуба — сегодня? после закрытия окна?
            # (б) текущего времени в окне (нормальная pending/missed логика).
            joined_in_club_tz = m.joined_at.astimezone(habit.tzinfo)
            joined_today_in_club_tz = joined_in_club_tz.date() == club_date
            if joined_today_in_club_tz and habit.was_joined_after_window(m.joined_at):
                # Новичок сегодня, окно уже закрыто → нельзя поймать (can_catch
                # станет False потому что status != 'missed'). На TodayPage самого
                # юзера показывается нейтральный текст.
                status = "joined_late"
            elif habit.is_within_checkin_window(now):
                status = "pending"
            else:
                status = "missed"

        # photo_url: relative путь, frontend обернёт в absolute URL
        # (Pravki §7.1 v3.1, nginx try_files на /api/v1/users/N/photo).
        photo_url = (
            f"/api/v1/users/{m.user_id}/photo"
            if m.user_id in user_id_to_photo
            else None
        )

        members.append(
            MemberRowOut(
                membership_id=str(m.id),
                user_id=m.user_id,
                first_name=user_id_to_name.get(m.user_id, "—"),
                username=None,
                status=status,
                checkin_count=counts.get(str(m.id), 0),
                can_catch=(
                    user.id != m.user_id
                    and status == "missed"
                    and str(m.id) not in penalty_set
                ),
                # Pravki-paused-frontend-2026-08-14: проброс актуального
                # membership.status в API для UX-фильтра во фронте.
                membership_status=m.status.value,
                photo_url=photo_url,
            )
        )

    return MembersResponse(items=members)


async def _user_names(session: AsyncSession, user_ids: list[int]) -> dict[int, str]:
    from sqlalchemy import select

    from app.models.user import User

    if not user_ids:
        return {}
    result = await session.execute(select(User.id, User.first_name).where(User.id.in_(user_ids)))
    return {row[0]: row[1] for row in result.all()}


async def _user_photo_file_ids(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, str]:
    """Возвращает {user_id: photo_file_id} для юзеров с фото.

    NULL photo_file_id → не возвращаем. Используется для построения
    photo_url на фронте (Pravki §7.1 v3.1).
    """
    from sqlalchemy import select

    from app.models.user import User

    if not user_ids:
        return {}
    result = await session.execute(
        select(User.id, User.photo_file_id).where(
            User.id.in_(user_ids), User.photo_file_id.is_not(None)
        )
    )
    return {row[0]: row[1] for row in result.all()}


@router.post("/habits/{habit_id}/catch", response_model=CatchResponse)
async def catch_violator(
    habit_id: str,
    payload: CatchRequest,
    user: TelegramUserDbDep,
    session: SessionDep,
    redis: RedisDep,
) -> CatchResponse:
    habit_repo = HabitRepository(session)
    membership_repo = MembershipRepository(session)
    checkin_repo = CheckinRepository(session)
    suspicious_repo = SuspiciousPairsRepository(session)
    service = PenaltyService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        suspicious_repo=suspicious_repo,
        redis_port=RedisCatchRateLimiter(redis),
    )

    catcher_membership = await membership_repo.get_for_user_in_habit(user.id, habit_id)
    club_date = (await habit_repo.get(habit_id)).club_date(datetime.now(tz=UTC))  # type: ignore[union-attr]

    # Pravki-bug-fixes §Z-21 (Item 6): для broadcast'а catch_event нужен
    # violator.user_id. Достаём ДО apply_catch (в той же сессии), чтобы
    # после commit() объект остался доступен без re-fetch.
    violator_membership = await membership_repo.get(payload.violator_membership_id)
    if violator_membership is None:
        # Race: юзер удалён между pre-filter и apply_catch. Не должно
        # случаться в норме (MembershipNotFoundError будет в service),
        # но защищаемся явно.
        return CatchResponse(ok=False, code="violator_membership_not_found")

    try:
        penalty = await service.apply_catch(
            catcher_user_id=user.id,
            violator_membership_id=payload.violator_membership_id,
            club_date=club_date,
            catcher_membership_id=str(catcher_membership.id) if catcher_membership else None,
        )
        await session.commit()
    except PenaltyAlreadyProcessedError as exc:
        await session.rollback()
        return CatchResponse(ok=False, code=exc.code)
    except IntegrityError:
        # Pravki-deposit-sse.md §Z-2.8: гонка двух параллельных catch'ей на одну
        # и ту же (membership_id, date, reason). UNIQUE-индекс uq_penalty_per_day_reason
        # (миграция 002) срабатывает на INSERT второй транзакции. session.commit()
        # бросает IntegrityError — для юзера это эквивалентно "уже обработано".
        await session.rollback()
        return CatchResponse(ok=False, code="penalty_already_processed")
    except Exception as exc:
        await session.rollback()
        from app.core.exceptions import DomainError

        if isinstance(exc, DomainError):
            return CatchResponse(ok=False, code=exc.code)
        raise

    # Pravki-bug-fixes §Z-21 (Item 6 + Item 8): после успешного commit() Penalty
    # уже в БД — это ФИНАНСОВЫЙ ИНВАРИАНТ, который НЕЛЬЗЯ ломать.
    # Два broadcast'а (Items 6 + 8): habit-stream (catch_event, для ВСЕХ
    # участников клуба) и user-stream violator'а (you_were_caught, personal).
    #
    # ОБЕРНУТЫ В РАЗДЕЛЬНЫЕ try/except — каждый broadcast независим:
    # если broker временно упал между первым и вторым send_task, восстановление
    # ко второму всё равно позволит publish_you_were_caught дойти до воркера.
    # НЕ объединять в один try — падение первого отменит второй.
    #
    # At-most-once на каждый: если брокер недоступен ИЛИ send_task бросает
    # исключение — warning-лог, НЕ ломаем HTTP-ответ catch_violator
    # (юзер уже видит CatchResponse.ok=True, penalty в БД).
    #
    # catcher_first_name берём из `user` (TelegramUserDbDep, JWT init_data) —
    # уже в scope, 0 round-trip'ов. violator_first_name worker добудет
    # отдельным PK lookup'ом (Variant C из разведки Item 8).
    log = get_logger("members.catch_violator")
    catcher_first_name: str = user.first_name or "User"
    violator_user_id: int = violator_membership.user_id

    # Broadcast #1: catch_event в habit-stream (для всех участников клуба).
    # Бэкенд не обогащает violator_first_name здесь — worker fetch'ит
    # отдельно (см. publish_catch_event._run).
    try:
        from app.services.celery_producer import send_task

        send_task(
            "publish_catch_event",
            {
                "habit_id": habit_id,
                "penalty_id": str(penalty.id),
                "catcher_user_id": user.id,
                "catcher_first_name": catcher_first_name,
                "violator_user_id": violator_user_id,
                "violator_membership_id": payload.violator_membership_id,
                "amount": penalty.amount,
            },
        )
    except Exception as exc:  # noqa: BLE001 — broadcast failure must not break HTTP response
        log.warning(
            "catch_publish_task_failed",
            extra={
                "habit_id": habit_id,
                "penalty_id": str(penalty.id),
                "event": "publish_catch_event",
                "err": str(exc),
                "err_type": exc.__class__.__name__,
            },
        )
        # НЕ пробрасываем исключение — catch уже успешен, broadcast
        # attempt для ВТОРОГО события (publish_you_were_caught) ещё впереди.

    # Broadcast #2: you_were_caught в user-stream violator'а (personal).
    # Отдельный try/except от broadcast #1 — независимые попытки.
    try:
        from app.services.celery_producer import send_task

        send_task(
            "publish_you_were_caught",
            {
                "user_id": violator_user_id,
                "membership_id": payload.violator_membership_id,
                "habit_id": habit_id,
                "catcher_user_id": user.id,
                "catcher_first_name": catcher_first_name,
                "amount": penalty.amount,
                "date_iso": club_date.isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001 — broadcast failure must not break HTTP response
        log.warning(
            "you_were_caught_publish_task_failed",
            extra={
                "habit_id": habit_id,
                "user_id": violator_user_id,
                "event": "publish_you_were_caught",
                "err": str(exc),
                "err_type": exc.__class__.__name__,
            },
        )
        # НЕ пробрасываем исключение — catch уже успешен.

    return CatchResponse(ok=True, amount=penalty.amount)
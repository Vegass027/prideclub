from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.core.exceptions import (
    CheckinAlreadyCaughtError,
    CheckinAlreadyExistsError,
    CheckinJoinedLateError,
    CheckinWindowClosedError,
    CheckinWrongTopicError,
    HabitArchivedError,
    HabitNotFoundError,
    MembershipNotActiveError,
    MembershipNotFoundError,
)
from app.core.logging import get_logger
from app.models.checkin import Checkin
from app.models.habit import Habit
from app.models.membership import Membership
from app.repositories.checkin_repository import CheckinRepository
from app.repositories.habit_repository import HabitRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.penalty_repository import PenaltyRepository
from app.services.proof_validator import ProofMessage, validate_proof_media


class CachePort(Protocol):
    """Redis-порт: ровно один метод — инвалидировать статус дня."""

    async def invalidate_today(self, habit_id: str, membership_id: str) -> None: ...


@dataclass(slots=True)
class TodayStats:
    """Сводка по юзеру в клубе для карточки Today (Pravki.md 2026-07-24).

    checkin_count — total done-чекинов за всё время.
    streak_days — consecutive от today назад (0 если сегодня не отмечен).
    penalties_count / penalties_total — антифрод: сколько раз поймали
    и сколько денег списали (в копейках).
    """

    status: str
    checkin_count: int
    streak_days: int
    penalties_count: int
    penalties_total: int


class CheckinService:
    """Бизнес-логика чек-ина.

    Все запросы — через репозитории, кэш — через порт CachePort.
    """

    def __init__(
        self,
        session,
        habit_repo: HabitRepository,
        membership_repo: MembershipRepository,
        checkin_repo: CheckinRepository,
        penalty_repo: PenaltyRepository,
        cache: CachePort | None = None,
    ) -> None:
        self._session = session
        self._habit_repo = habit_repo
        self._membership_repo = membership_repo
        self._checkin_repo = checkin_repo
        self._penalty_repo = penalty_repo
        self._cache = cache
        self._logger = get_logger("checkin_service")

    async def process_checkin(
        self,
        *,
        user_id: int,
        habit_id: str,
        proof: ProofMessage,
        proof_message_id: int,
        now_utc: datetime,
        message_thread_id: int | None = None,
    ) -> tuple[Checkin, bool]:
        habit = await self._habit_repo.get(habit_id)
        if habit is None:
            raise HabitNotFoundError()

        membership = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if membership is None:
            raise MembershipNotFoundError()
        if membership.status.value != "active":
            raise MembershipNotActiveError()

        # Topic-scoped (migration 010): если у клуба задан
        # checkin_topic_thread_id — принимаем только сообщения из этого
        # топика. Иначе (старый режим) — принимаем всё в чате клуба.
        if (
            habit.checkin_topic_thread_id is not None
            and message_thread_id != habit.checkin_topic_thread_id
        ):
            self._logger.info(
                "checkin_wrong_topic",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "expected_thread_id": habit.checkin_topic_thread_id,
                    "got_thread_id": message_thread_id,
                },
            )
            raise CheckinWrongTopicError()

        # Антифрод — медиа должно соответствовать типу привычки.
        # proof.proof_type приходит из бота и проверяется здесь как
        # независимый от habit источник.
        # Multi-proof (migration 012): проверяем вхождение в массив разрешённых.
        validate_proof_media(proof, max_age_seconds=60)
        if proof.proof_type.value not in (habit.proof_types or []):
            from app.services.proof_validator import ProofValidationError

            raise ProofValidationError("wrong_type")

        # Pravki-bug-fixes §Z-19 (joiner-late protection): симметричная серверная
        # защита от race / старого бота / прямого вызова / etc.
        # ВАЖНО: проверяем joined_late ПЕРЕД обычной window check — иначе
        # для новичка вступившего в 13:00 (окно 06-12) сначала сработал бы
        # CheckinWindowClosedError (status_code == code "checkin_window_closed"),
        # и joined_late никогда бы не был достигнут. Новичок должен получить
        # специфическое сообщение "ваш первый чек-ин завтра", а не общее
        # "окно закрыто".
        # Запрос к membership.joined_at СВЕЖИЙ из БД (не из кеша) — это
        # race-safe: если юзер вступил между pre-filter бота и этой проверкой,
        # мы увидим актуальное состояние.
        club_date = habit.club_date(now_utc)

        # Pravki-bug-fixes §Z-21 (Item 4): defense-in-depth — если за club_date
        # уже есть Penalty (CAUGHT или WINDOW_CLOSED_NO_CATCH), чек-ин невозможен.
        # Бот в pre-filter должен отсеять (state.caught_today), но это fallback
        # на race / bypass / старую версию бота / прямой вызов internal API.
        #
        # Семантика НЕ различает CAUGHT vs WINDOW_CLOSED_NO_CATCH на этом уровне:
        # оба означают «штраф за день уже списан, чек-ин не принимается».
        # Различение делается в UI через StatusBadge (Item 3) и на фронте TodayPage.
        # Бот использует catch_today + checkin_status для разных текстов.
        #
        # ВАЖНО: идёт ПОСЛЕ joined_at check (выше) и ДО window check (ниже),
        # потому что:
        # - joined_late сначала возвращает специфический текст «ваш первый чек-ин
        #   завтра» (а не «штраф списан»);
        # - window-closed без cron penalty сначала возвращает «окно закрыто»
        #   (а не «штраф списан»).
        # Только если club_date прошёл joined_late AND прошёл window AND
        # cron отработал (или apply_catch был) — сюда попадаем.
        if await self._penalty_repo.has_any_penalty_today(
            membership_id=membership.id,
            club_date=club_date,
        ):
            self._logger.info(
                "checkin_rejected_caught_today",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "membership_id": membership.id,
                    "club_date": str(club_date),
                },
            )
            raise CheckinAlreadyCaughtError()

        # Defensive: joined_at=None пропускается (только в тестах).
        if membership.joined_at is not None:
            joined_in_club_tz = membership.joined_at.astimezone(habit.tzinfo)
            if (
                joined_in_club_tz.date() == club_date
                and habit.was_joined_after_window(membership.joined_at)
            ):
                self._logger.info(
                    "checkin_rejected_joined_late",
                    extra={
                        "user_id": user_id,
                        "habit_id": habit_id,
                        "joined_at_utc": membership.joined_at.isoformat(),
                        "club_date": str(club_date),
                    },
                )
                raise CheckinJoinedLateError()

        # Окно чек-ина — в TZ клуба. Идёт ПОСЛЕ joined_late чтобы новичок
        # получил специфический код, а не общий "checkin_window_closed".
        if not habit.is_within_checkin_window(now_utc):
            raise CheckinWindowClosedError()

        try:
            checkin, created = await self._checkin_repo.get_or_create_done(
                membership_id=membership.id,
                on_date=club_date,
                proof_message_id=proof_message_id,
            )
        except Exception as exc:  # noqa: BLE001
            from sqlalchemy.exc import IntegrityError

            if isinstance(exc, IntegrityError):
                raise CheckinAlreadyExistsError() from exc
            raise

        if not created:
            # Уже был чек-ин сегодня — это идемпотентный ответ, не ошибка.
            self._logger.info(
                "checkin_duplicate",
                extra={
                    "user_id": user_id,
                    "habit_id": habit_id,
                    "date": str(club_date),
                },
            )

        if self._cache is not None:
            try:
                await self._cache.invalidate_today(habit.id, membership.id)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "cache_invalidate_failed",
                    extra={"err": str(exc), "habit_id": habit_id},
                )

        self._logger.info(
            "checkin_processed",
            extra={
                "user_id": user_id,
                "habit_id": habit_id,
                "membership_id": membership.id,
                "date": str(club_date),
                "created": created,
            },
        )
        return checkin, created

    async def get_today_status(
        self, *, user_id: int, habit_id: str, now_utc: datetime
    ) -> tuple[Habit, Membership, TodayStats]:
        """Возвращает (habit, membership, TodayStats).

        Pravki.md 2026-07-24: расширено — добавили checkin_count (total
        done), penalties_count и penalties_total для карточки клуба.
        streak_days остался как consecutive (мотивационная метрика).

        T4: SQL не делаем напрямую — все запросы через репозитории
        (CheckinRepository / PenaltyRepository). Это позволяет
        подменять их на FakeRepo в тестах.
        """
        habit = await self._habit_repo.get(habit_id)
        if habit is None or habit.archived_at is not None:
            raise HabitArchivedError()
        membership = await self._membership_repo.get_for_user_in_habit(user_id, habit_id)
        if membership is None:
            raise MembershipNotFoundError()

        club_date = habit.club_date(now_utc)
        existing = await self._checkin_repo.get_for_date(membership.id, club_date)

        if existing is not None:
            status = existing.status.value
        else:
            # Defensive: joined_at может быть None в тестах (FakeMembershipRepo
            # не задаёт поле). В проде NOT NULL constraint + server_default
            # гарантируют значение, но мы не падаем на None.
            joined_at = membership.joined_at
            status = None
            if joined_at is not None:
                # Pravki-bug-fixes §Z-19 (joiner-late protection):
                # joined_late имеет приоритет над pending/missed — даже если окно
                # случайно открыто (race в TZ, DST и т.п.), joined_late остаётся.
                # Это status для TodayResponse — UI TodayPage (Z-19.5) показывает
                # отдельный блок для joined_late.
                joined_in_club_tz = joined_at.astimezone(habit.tzinfo)
                if (
                    joined_in_club_tz.date() == club_date
                    and habit.was_joined_after_window(joined_at)
                ):
                    status = "joined_late"
            if status is None:
                if habit.is_within_checkin_window(now_utc):
                    status = "pending"
                else:
                    status = "missed"

        checkin_count = await self._checkin_repo.count_done_for_membership(
            str(membership.id)
        )
        penalties_count, penalties_total = await self._penalty_repo.totals_for_membership(
            str(membership.id), as_violator=True
        )
        streak = await self._compute_streak(membership.id, club_date)
        stats = TodayStats(
            status=status,
            checkin_count=checkin_count,
            streak_days=streak,
            penalties_count=penalties_count,
            penalties_total=penalties_total,
        )
        return habit, membership, stats

    async def _compute_streak(self, membership_id: str, up_to) -> int:
        """Считаем серию done-чекинов до up_to.

        До T4 — SELECT к Checkin делался прямо в сервисе. После T4 —
        только Python-цикл по датам из CheckinRepository.get_recent_dates().
        """
        dates = await self._checkin_repo.get_recent_dates(membership_id, up_to)
        if not dates:
            return 0
        streak = 0
        expected = up_to
        for d in dates:
            if d == expected:
                streak += 1
                expected = expected - timedelta(days=1)
            else:
                break
        return streak
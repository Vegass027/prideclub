from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.core.constants import CheckinStatus, MembershipStatus, ProofType
from app.core.exceptions import (
    CheckinWindowClosedError,
)
from app.models.checkin import Checkin
from app.models.penalty import Penalty
from app.services.checkin_service import CheckinService
from app.services.proof_validator import ProofMessage, ProofValidationError
from tests.fakes import (
    FakeCache,
    FakeCheckinRepo,
    FakeHabitRepo,
    FakeMembershipRepo,
    FakePenaltyRepo,
    FakeSession,
    make_habit,
)


def _proof(*, duration: int | None = 5, msg_date: datetime | None = None) -> ProofMessage:
    return ProofMessage(
        proof_type=ProofType.VIDEO_NOTE,
        video_note_duration=duration,
        photo_sizes=0,
        message_date=msg_date or datetime.now(tz=UTC),
    )


@pytest.mark.asyncio
async def test_checkin_happy_path() -> None:
    habit = make_habit(proof=ProofType.VIDEO_NOTE)
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    cache = FakeCache()
    session = FakeSession(checkin_repo)

    service = CheckinService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
        cache=cache,  # type: ignore[arg-type]
    )

    checkin, _ = await _wrap(service.process_checkin(
        user_id=1,
        habit_id=str(habit.id),
        proof=_proof(),
        proof_message_id=42,
        now_utc=datetime.now(tz=UTC),
    ))
    assert checkin.status.value == "done"
    assert checkin.proof_message_id == 42
    assert cache.invalidated == [(str(habit.id), str(m.id))]


@pytest.mark.asyncio
async def test_checkin_idempotent_same_day() -> None:
    habit = make_habit(proof=ProofType.VIDEO_NOTE)
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    session = FakeSession(checkin_repo)
    service = CheckinService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    now = datetime.now(tz=UTC)
    await _wrap(service.process_checkin(
        user_id=1,
        habit_id=str(habit.id),
        proof=_proof(msg_date=now),
        proof_message_id=1,
        now_utc=now,
    ))
    second, created = await _wrap(service.process_checkin(
        user_id=1,
        habit_id=str(habit.id),
        proof=_proof(msg_date=now),
        proof_message_id=2,
        now_utc=now,
    ))
    assert created is False
    assert second.proof_message_id == 1  # оригинальное сообщение


@pytest.mark.asyncio
async def test_checkin_rejects_paused_membership() -> None:
    """Pravki §Z-22 (Step 3, hole #3): paused → CheckinMembershipPausedError.

    Раньше (до Шага 3) этот же тест ассертил MembershipNotActiveError.
    После сплита — отдельный код paused/left, потому что тексты для юзера
    разные (пополни депозит vs вступи заново).
    """
    from app.core.exceptions import CheckinMembershipPausedError

    habit = make_habit()
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.PAUSED
    )
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    with pytest.raises(CheckinMembershipPausedError) as exc_info:
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(),
            proof_message_id=1,
            now_utc=datetime.now(tz=UTC),
        )
    assert exc_info.value.code == "membership_paused"


@pytest.mark.asyncio
async def test_checkin_rejects_left_membership() -> None:
    """Pravki §Z-22 (Step 3, hole #3): left → CheckinMembershipLeftError."""
    from app.core.exceptions import CheckinMembershipLeftError

    habit = make_habit()
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.LEFT
    )
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    with pytest.raises(CheckinMembershipLeftError) as exc_info:
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(),
            proof_message_id=1,
            now_utc=datetime.now(tz=UTC),
        )
    assert exc_info.value.code == "membership_left"


@pytest.mark.asyncio
async def test_checkin_rejects_expired_subscription() -> None:
    """Pravki-subscription-2026-08-17 §Z-22 (canonical #6): subscription_until < club_date
    → CheckinSubscriptionExpiredError. Сравнение по club_date в TZ клуба.

    Worker race-fallback (defense-in-depth): если бот bypass / старая версия /
    прямой вызов — worker тоже режет, чтобы не было ложного "Принято".
    """
    from datetime import timedelta

    from app.core.exceptions import CheckinSubscriptionExpiredError

    habit = make_habit()
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.ACTIVE
    )
    # Подписка истекла 5 дней назад по club_date.
    m.subscription_until = habit.club_date(datetime.now(tz=UTC)) - timedelta(days=5)
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    with pytest.raises(CheckinSubscriptionExpiredError) as exc_info:
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(),
            proof_message_id=1,
            now_utc=datetime.now(tz=UTC),
        )
    assert exc_info.value.code == "subscription_expired"


@pytest.mark.asyncio
async def test_checkin_subscription_priority_over_paused_left() -> None:
    """Pravki-subscription-2026-08-17 §Z-22: combo status=paused + sub expired
    → CheckinSubscriptionExpiredError (НЕ CheckinMembershipPausedError).

    Семантика: "продли подписку" лечит и подписку, и (через recompute пауз)
    возможный PAUSED. "Пополни депозит" лечит ТОЛЬКО PAUSED, а подписку не
    лечит → пользователь зациклится на ошибке PAUSED после topup.
    """
    from datetime import timedelta

    from app.core.exceptions import CheckinSubscriptionExpiredError

    habit = make_habit()
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.PAUSED
    )
    m.subscription_until = habit.club_date(datetime.now(tz=UTC)) - timedelta(days=3)
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    with pytest.raises(CheckinSubscriptionExpiredError) as exc_info:
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(),
            proof_message_id=1,
            now_utc=datetime.now(tz=UTC),
        )
    # ВАЖНО: subscription_expired, НЕ membership_paused.
    assert exc_info.value.code == "subscription_expired"


@pytest.mark.asyncio
async def test_checkin_subscription_today_last_day_passes() -> None:
    """Pravki-subscription-2026-08-17 Q2: subscription_until == club_date → ещё валиден.
    "День-в-день, без grace period" — сегодня последний день подписки, чек-ин разрешён.
    """
    habit = make_habit()
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(
        user_id=1, habit_id=str(habit.id), status=MembershipStatus.ACTIVE
    )
    m.subscription_until = habit.club_date(datetime.now(tz=UTC))  # today
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    # Не должно быть subscription_expired; следующая проверка (window_closed)
    # тоже не должна сработать (окно открыто 06:00–22:00 по умолчанию,
    # current time UTC < 22:00). Проверяем happy path через PenaltyRepository
    # return None (no penalty today) → idem создаст Checkin.
    c, created = await service.process_checkin(
        user_id=1,
        habit_id=str(habit.id),
        proof=_proof(),
        proof_message_id=1,
        now_utc=datetime.now(tz=UTC),
    )
    assert created is True


@pytest.mark.asyncio
async def test_checkin_window_closed() -> None:
    from datetime import time

    habit = make_habit()
    habit.checkin_window_start = time(8, 0)
    habit.checkin_window_end = time(9, 0)
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    # 12:00 по Москве → вне окна (8:00–9:00 МСК)
    # msg_date оставляем "сейчас", чтобы не словить stale_message раньше окна.
    now = datetime.now(tz=UTC)
    habit.timezone = "Europe/Moscow"

    with pytest.raises(CheckinWindowClosedError):
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(msg_date=now),
            proof_message_id=1,
            now_utc=now.replace(hour=12, minute=0, second=0, microsecond=0),
        )


@pytest.mark.asyncio
async def test_checkin_wrong_proof_type() -> None:
    habit = make_habit(proof=ProofType.PHOTO)
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    with pytest.raises(ProofValidationError) as exc:
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(),  # video_note, но habit ждёт photo
            proof_message_id=1,
            now_utc=datetime.now(tz=UTC),
        )
    assert exc.value.code == "wrong_type"


@pytest.mark.asyncio
async def test_checkin_accepts_proof_type_in_proof_types() -> None:
    """Миграция 012: если habit принимает [video_note, photo] — кружок ОК."""
    habit = make_habit(
        proof=ProofType.VIDEO_NOTE,
        proof_types=[ProofType.VIDEO_NOTE.value, ProofType.PHOTO.value],
    )
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )
    checkin, created = await service.process_checkin(
        user_id=1,
        habit_id=str(habit.id),
        proof=_proof(),
        proof_message_id=1,
        now_utc=datetime.now(tz=UTC),
    )
    assert created is True
    assert checkin.status == CheckinStatus.DONE


@pytest.mark.asyncio
async def test_checkin_rejects_proof_type_not_in_proof_types() -> None:
    """Миграция 012: habit принимает [photo, text] — кружок отклонён."""
    habit = make_habit(
        proof=ProofType.PHOTO,
        proof_types=[ProofType.PHOTO.value, ProofType.TEXT.value],
    )
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    service = CheckinService(
        session=FakeSession(checkin_repo),
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )
    with pytest.raises(ProofValidationError) as exc:
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(),  # video_note — не в списке разрешённых
            proof_message_id=1,
            now_utc=datetime.now(tz=UTC),
        )
    assert exc.value.code == "wrong_type"


async def _wrap(coro):
    return await coro


@pytest.mark.asyncio
async def test_get_today_status_streak_counts_consecutive_done_days() -> None:
    """Streak = подряд идущие done-дни, заканчивающиеся club_date.

    T4: использует FakeCheckinRepo.get_recent_dates — без SELECT
    через session.execute.
    """
    habit = make_habit(proof=ProofType.VIDEO_NOTE)
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    session = FakeSession(checkin_repo)

    # 3 done-дня подряд: 2026-01-10, 2026-01-09, 2026-01-08.
    # 2026-01-07 — pending/missing → streak прерывается на 3.
    for d in (date(2026, 1, 10), date(2026, 1, 9), date(2026, 1, 8)):
        checkin_repo._store[(str(m.id), d)] = Checkin(
            id=str(uuid4()),
            membership_id=str(m.id),
            date=d,
            status=CheckinStatus.DONE,
            proof_message_id=1,
        )

    service = CheckinService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    # now_utc = 2026-01-09 21:00 UTC = 2026-01-10 00:00 МСК → club_date = 2026-01-10
    _habit, _m, stats = await service.get_today_status(
        user_id=1,
        habit_id=str(habit.id),
        now_utc=datetime(2026, 1, 9, 21, 0, tzinfo=UTC),
    )
    assert stats.streak_days == 3
    assert stats.status == "done"
    assert stats.checkin_count == 3


@pytest.mark.asyncio
async def test_get_today_status_streak_zero_when_no_checkins() -> None:
    habit = make_habit()
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    session = FakeSession(checkin_repo)

    service = CheckinService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    _habit, _m, stats = await service.get_today_status(
        user_id=1,
        habit_id=str(habit.id),
        now_utc=datetime(2026, 1, 9, 15, 0, tzinfo=UTC),
    )
    assert stats.streak_days == 0
    assert stats.checkin_count == 0
    assert stats.penalties_count == 0
    assert stats.penalties_total == 0
    # окно чек-ина [09:00-21:00] MSK в make_habit — открыто в 15:00 МСК = 12:00 UTC
    assert stats.status == "pending"


@pytest.mark.asyncio
async def test_get_today_status_penalty_for_today_kopecks_zero_when_no_penalties() -> None:
    """Pravki-paused-window-open-2026-08-14: `penalty_for_today_kopecks`
    должен быть 0 если за club_date нет ни одного Penalty.

    TodayPage использует это поле для условного рендера: если 0 и
    status="missed" — показывает "штраф не списан, депозит пуст".
    Без явного теста будущий рефакторинг `amount_for_today` может
    сломать контракт (например, вернуть None вместо 0), и UI снова
    начнёт врать.
    """
    habit = make_habit()
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    session = FakeSession(checkin_repo)

    service = CheckinService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    _habit, _m, stats = await service.get_today_status(
        user_id=1,
        habit_id=str(habit.id),
        now_utc=datetime.now(tz=UTC),
    )
    assert stats.penalty_for_today_kopecks == 0


@pytest.mark.asyncio
async def test_get_today_status_penalty_for_today_kopecks_reflects_today_penalties() -> None:
    """Pravki-paused-window-open-2026-08-14: penalty за сегодня
    (через apply_catch или apply_window_expired) корректно отражается
    в `penalty_for_today_kopecks`. Пенальти за другие дни НЕ должны
    попадать — поле строго за club_date.
    """
    habit = make_habit()
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    m = membership_repo.add_for(user_id=1, habit_id=str(habit.id))
    checkin_repo = FakeCheckinRepo()
    penalty_repo = FakePenaltyRepo()
    session = FakeSession(checkin_repo)

    # Penalty за club_date (попадает в today_penalty_for_kopecks)
    penalty_repo.add(
        Penalty(
            id=str(uuid4()),
            membership_id=str(m.id),
            catcher_membership_id=None,
            amount=12500,  # = 125₽
            fund_share=12500,
            catcher_bonus_points=0,
            reason="caught",
            date=date.today(),
            bonus_applied=False,
        )
    )
    # Penalty за ВЧЕРА (НЕ должно попадать)
    from datetime import timedelta

    yesterday = date.today() - timedelta(days=1)
    penalty_repo.add(
        Penalty(
            id=str(uuid4()),
            membership_id=str(m.id),
            catcher_membership_id=None,
            amount=99999,
            fund_share=99999,
            catcher_bonus_points=0,
            reason="caught",
            date=yesterday,
            bonus_applied=False,
        )
    )

    service = CheckinService(
        session=session,
        habit_repo=habit_repo,
        membership_repo=membership_repo,
        checkin_repo=checkin_repo,
        penalty_repo=penalty_repo,
    )

    _habit, _m, stats = await service.get_today_status(
        user_id=1,
        habit_id=str(habit.id),
        now_utc=datetime.now(tz=UTC),
    )
    assert stats.penalty_for_today_kopecks == 12500, (
        f"Ожидали 12500 (только сегодняшний penalty), получили {stats.penalty_for_today_kopecks}. "
        f"Старый penalty за вчера НЕ должен попадать."
    )
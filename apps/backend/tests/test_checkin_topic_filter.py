from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.constants import ProofType
from app.core.exceptions import CheckinWrongTopicError
from app.services.checkin_service import CheckinService
from app.services.proof_validator import ProofMessage
from tests.fakes import (
    FakeCache,
    FakeCheckinRepo,
    FakeHabitRepo,
    FakeMembershipRepo,
    FakePenaltyRepo,
    FakeSession,
    make_habit,
)


def _proof() -> ProofMessage:
    return ProofMessage(
        proof_type=ProofType.VIDEO_NOTE,
        video_note_duration=5,
        photo_sizes=0,
        message_date=datetime.now(tz=UTC),
    )


async def _wrap(awaitable):
    """Run coroutine and return result, raising on exceptions."""
    return await awaitable


@pytest.mark.asyncio
async def test_topic_scoped_checkin_accepts_matching_thread() -> None:
    habit = make_habit(chat_id=4348250990)
    habit.checkin_topic_thread_id = 1
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
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

    checkin, _ = await service.process_checkin(
        user_id=1,
        habit_id=str(habit.id),
        proof=_proof(),
        proof_message_id=42,
        now_utc=datetime.now(tz=UTC),
        message_thread_id=1,
    )
    assert checkin is not None


@pytest.mark.asyncio
async def test_topic_scoped_checkin_rejects_wrong_thread() -> None:
    habit = make_habit(chat_id=4348250990)
    habit.checkin_topic_thread_id = 1
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
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

    with pytest.raises(CheckinWrongTopicError):
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(),
            proof_message_id=42,
            now_utc=datetime.now(tz=UTC),
            message_thread_id=2,
        )


@pytest.mark.asyncio
async def test_topic_scoped_checkin_rejects_general_when_topic_required() -> None:
    """Сообщение в General (message_thread_id=None) при заданном топике — отказ."""
    habit = make_habit(chat_id=4348250990)
    habit.checkin_topic_thread_id = 1
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
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

    with pytest.raises(CheckinWrongTopicError):
        await service.process_checkin(
            user_id=1,
            habit_id=str(habit.id),
            proof=_proof(),
            proof_message_id=42,
            now_utc=datetime.now(tz=UTC),
            message_thread_id=None,
        )


@pytest.mark.asyncio
async def test_no_topic_checkin_accepts_any_thread() -> None:
    """Если checkin_topic_thread_id IS NULL — старый режим (любой топик)."""
    habit = make_habit(chat_id=4348250990)
    assert habit.checkin_topic_thread_id is None
    habit_repo = FakeHabitRepo()
    habit_repo.add(habit)
    membership_repo = FakeMembershipRepo()
    membership_repo.add_for(user_id=1, habit_id=str(habit.id))
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

    checkin, _ = await service.process_checkin(
        user_id=1,
        habit_id=str(habit.id),
        proof=_proof(),
        proof_message_id=42,
        now_utc=datetime.now(tz=UTC),
        message_thread_id=999,
    )
    assert checkin is not None
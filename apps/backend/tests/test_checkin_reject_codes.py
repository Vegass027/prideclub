"""Pravki §Z-22 (prefilter holes, 5-round fix): защита от дрейфа между
классом исключения и CheckinRejectCode enum.

Шаг 0 проверяет ТОЛЬКО то, что реально существует:
- 5 классов из worker except-блока, которые получают enum
- + legacy MembershipNotActiveError (catch-flow, остаётся в enum для SoT)

В Шаге 3 добавятся пары для CheckinMembershipPausedError/LeftError —
они появляются только в Шаге 3, нельзя проверять их в Шаге 0.
"""
from __future__ import annotations

import pytest

from app.core.constants import CheckinRejectCode
from app.core.exceptions import (
    CheckinAlreadyCaughtError,
    CheckinJoinedLateError,
    CheckinWindowClosedError,
    CheckinWrongTopicError,
    MembershipNotActiveError,
    MembershipNotFoundError,
)


def test_all_exception_codes_match_enum() -> None:
    """Каждый class.code == CheckinRejectCode.X.value (защита от дрейфа).

    Порядок пар НЕ важен (это coverage-проверка), но сами пары —
    единственный источник правды. Если кто-то переименовал enum-key
    или поменял class.code — этот тест упадёт.
    """
    pairs = [
        # Чек-ин путь (используется и в bot, и в backend, и в worker)
        (CheckinWindowClosedError, CheckinRejectCode.WINDOW_CLOSED),
        (CheckinJoinedLateError, CheckinRejectCode.JOINED_LATE),
        (CheckinAlreadyCaughtError, CheckinRejectCode.ALREADY_CAUGHT),
        (CheckinWrongTopicError, CheckinRejectCode.WRONG_TOPIC),
        (MembershipNotFoundError, CheckinRejectCode.MEMBERSHIP_NOT_FOUND),
        # Legacy для catch-flow (НЕ чек-ин). MembershipNotActiveError.code
        # остаётся "membership_not_active" — он в enum для единого SoT,
        # но в чек-ин потоке НЕ используется (Шаг 3 сплитит).
        (MembershipNotActiveError, CheckinRejectCode.MEMBERSHIP_NOT_ACTIVE),
    ]
    for cls, enum_member in pairs:
        assert cls.code == enum_member.value, (
            f"{cls.__name__}.code={cls.code!r} != enum value {enum_member.value!r}. "
            f"Обнови class.code или enum — drift между ними недопустим."
        )


def test_proof_validator_codes_are_enum_values() -> None:
    """Все raise ProofValidationError(...) внутри proof_validator
    получают enum.value (защита от magic-string дрейфа внутри валидатора).
    """
    from datetime import UTC, datetime

    from app.core.constants import ProofType
    from app.services.proof_validator import (
        ProofMessage,
        ProofValidationError,
        validate_proof_media,
    )

    # 1. VIDEO_NOTE без duration → WRONG_TYPE
    with pytest.raises(ProofValidationError) as exc_info:
        validate_proof_media(
            ProofMessage(proof_type=ProofType.VIDEO_NOTE, video_note_duration=None)
        )
    assert exc_info.value.code == CheckinRejectCode.WRONG_TYPE.value

    # 2. VIDEO_NOTE duration < 3 → TOO_SHORT
    with pytest.raises(ProofValidationError) as exc_info:
        validate_proof_media(ProofMessage(proof_type=ProofType.VIDEO_NOTE, video_note_duration=1))
    assert exc_info.value.code == CheckinRejectCode.TOO_SHORT.value

    # 3. PHOTO без sizes → WRONG_TYPE
    with pytest.raises(ProofValidationError) as exc_info:
        validate_proof_media(ProofMessage(proof_type=ProofType.PHOTO, photo_sizes=0))
    assert exc_info.value.code == CheckinRejectCode.WRONG_TYPE.value

    # 4. TEXT с пустой строкой → EMPTY_TEXT
    with pytest.raises(ProofValidationError) as exc_info:
        validate_proof_media(ProofMessage(proof_type=ProofType.TEXT, text=""))
    assert exc_info.value.code == CheckinRejectCode.EMPTY_TEXT.value

    # 5. TEXT только whitespace → EMPTY_TEXT
    with pytest.raises(ProofValidationError) as exc_info:
        validate_proof_media(ProofMessage(proof_type=ProofType.TEXT, text="   \n"))
    assert exc_info.value.code == CheckinRejectCode.EMPTY_TEXT.value

    # 6. Forwarded message → FORWARDED
    with pytest.raises(ProofValidationError) as exc_info:
        validate_proof_media(
            ProofMessage(
                proof_type=ProofType.VIDEO_NOTE,
                video_note_duration=10,
                forward_date=datetime.now(tz=UTC),
            )
        )
    assert exc_info.value.code == CheckinRejectCode.FORWARDED.value

    # 7. Stale message (message_date > 60s ago) → STALE_MESSAGE
    with pytest.raises(ProofValidationError) as exc_info:
        validate_proof_media(
            ProofMessage(
                proof_type=ProofType.VIDEO_NOTE,
                video_note_duration=10,
                message_date=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
    assert exc_info.value.code == CheckinRejectCode.STALE_MESSAGE.value


def test_checkin_reject_code_order_matches_documented_priority() -> None:
    """Документированный в docstring enum порядок = фактический порядок
    итерации. Защита от тихого рефакторинга, который переставил ключи
    и нарушил canonical priority (1-11).

    Порядок в StrEnum = порядок итерации == порядок объявления в Python.
    """
    expected_order = [
        "habit_not_found",
        "membership_not_found",
        "membership_not_active",
        "membership_paused",
        "membership_left",
        "checkin_window_closed",
        "not_checkin_topic",
        "forwarded",
        "caught_today",
        "checkin_already_exists",
        "joined_late",
        "wrong_type",
        "too_short",
        "stale_message",
        "empty",
    ]
    actual_order = [m.value for m in CheckinRejectCode]
    assert actual_order == expected_order, (
        f"Drift между docstring enum'а и фактическим порядком ключей. "
        f"Переставь ключи в enum или поправь комментарий "
        f"(см. apps/backend/app/core/constants.py:CheckinRejectCode). "
        f"Expected: {expected_order}, got: {actual_order}"
    )


def test_no_duplicate_codes_in_enum() -> None:
    """Защита от случайного дубликата значения (например, кто-то скопипастил)."""
    values = [m.value for m in CheckinRejectCode]
    assert len(values) == len(set(values)), (
        f"Дубликаты в CheckinRejectCode: "
        f"{[v for v in values if values.count(v) > 1]}"
    )

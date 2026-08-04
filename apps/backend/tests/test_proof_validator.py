from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.constants import ProofType
from app.services.proof_validator import ProofMessage, ProofValidationError, validate_proof_media


def _msg(**kw) -> ProofMessage:
    base = {
        "proof_type": ProofType.VIDEO_NOTE,
        "video_note_duration": 5,
        "photo_sizes": 0,
        "message_date": datetime.now(tz=UTC),
    }
    base.update(kw)
    return ProofMessage(**base)


def test_video_note_too_short() -> None:
    with pytest.raises(ProofValidationError) as exc:
        validate_proof_media(_msg(video_note_duration=2))
    assert exc.value.code == "too_short"


def test_photo_missing() -> None:
    with pytest.raises(ProofValidationError) as exc:
        validate_proof_media(_msg(proof_type=ProofType.PHOTO, photo_sizes=0))
    assert exc.value.code == "wrong_type"


def test_text_empty() -> None:
    with pytest.raises(ProofValidationError) as exc:
        validate_proof_media(_msg(proof_type=ProofType.TEXT, text="   "))
    assert exc.value.code == "empty"


def test_forwarded_rejected() -> None:
    with pytest.raises(ProofValidationError) as exc:
        validate_proof_media(_msg(forward_date=datetime.now(tz=UTC)))
    assert exc.value.code == "forwarded"


def test_stale_message_rejected() -> None:
    from datetime import timedelta

    old = datetime.now(tz=UTC) - timedelta(hours=1)
    with pytest.raises(ProofValidationError) as exc:
        validate_proof_media(_msg(message_date=old))
    assert exc.value.code == "stale_message"


def test_happy_video_note() -> None:
    validate_proof_media(_msg())  # no raise
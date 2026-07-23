from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.constants import ProofType


@dataclass(slots=True, frozen=True)
class ProofMessage:
    """Унифицированное представление сообщения-доказательства.

    Aiogram-объекты не должны протекать в сервисный слой — здесь только данные.
    """

    proof_type: ProofType
    text: str | None = None
    video_note_duration: int | None = None
    photo_sizes: int = 0
    forward_date: datetime | None = None
    message_date: datetime | None = None


class ProofValidationError(Exception):
    """Поднимается если медиа не проходит антифрод-валидацию.

    Code — стабильный строковый идентификатор для фронта.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_proof_media(message: ProofMessage, *, max_age_seconds: int = 60) -> None:
    """Соглашение из docs/06-data-model.md §4.2.

    Дополнительно: message.date должен быть "сейчас" (защита от backdating).
    """
    pt = message.proof_type
    if pt == ProofType.VIDEO_NOTE:
        if message.video_note_duration is None:
            raise ProofValidationError("wrong_type")
        if message.video_note_duration < 3:
            raise ProofValidationError("too_short")
    elif pt == ProofType.PHOTO:
        if message.photo_sizes <= 0:
            raise ProofValidationError("wrong_type")
    elif pt == ProofType.TEXT:
        if not (message.text and message.text.strip()):
            raise ProofValidationError("empty")
    else:
        raise ProofValidationError("wrong_type")

    if message.forward_date is not None:
        raise ProofValidationError("forwarded")

    if message.message_date is not None:

        delta = (datetime.now(tz=UTC) - message.message_date).total_seconds()
        if delta < -5 or delta > max_age_seconds:
            raise ProofValidationError("stale_message")
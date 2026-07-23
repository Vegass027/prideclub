"""Unit-тесты для apps/bot/bot/handlers/checkin.py.

Проверяет:
- happy path (ok=True, code=None) → «Принято»
- happy path (ok=True, code='checkin_already_exists') → «Принято»
- код 'forwarded' → текст про пересылку
- код 'too_short' → текст про длительность
- код 'wrong_topic' → текст про топик
- код 'out_of_window' → текст про окно
- код 'habit_not_found' → ничего не отвечаем
- network exception → NETWORK_FAIL
- неизвестный код → REJECT_UNKNOWN

Все тесты изолированы: aiogram Message замокан, BackendClient — fake.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from bot.handlers import checkin_texts
from bot.handlers.checkin import _parse_proof, _text_for_code, handle_proof

# ---------- Fakes -----------------------------------------------------------


@dataclass(slots=True)
class FakeVideoNote:
    duration: int


@dataclass(slots=True)
class FakeMessage:
    chat_id: int = -1004467477629
    message_id: int = 42
    message_thread_id: int = 4
    from_user_id: int = 7295309649
    date: Any = None
    text: str | None = None
    photo: Any = None
    video_note: FakeVideoNote | None = None

    @property
    def chat(self) -> _FakeChat:
        return _FakeChat(id=self.chat_id, type="supergroup")

    @property
    def from_user(self) -> _FakeFromUser:
        return _FakeFromUser(id=self.from_user_id)


@dataclass(slots=True)
class _FakeChat:
    id: int
    type: str


@dataclass(slots=True)
class _FakeFromUser:
    id: int


@dataclass(slots=True)
class FakeBot:
    """Минимальный fake aiogram.Bot: записывает вызовы send_message."""
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send_message(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)


@dataclass(slots=True)
class FakeBackendClient:
    """Fake BackendClient: возвращает заранее заданный JSON или бросает."""
    response: dict[str, Any] | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"path": path, "json": json})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


# ---------- Helpers ---------------------------------------------------------


def _make_video_note_message() -> FakeMessage:
    return FakeMessage(video_note=FakeVideoNote(duration=5))


def _make_text_message() -> FakeMessage:
    return FakeMessage(text="чек")


def _make_photo_message() -> FakeMessage:
    return FakeMessage(photo=[{"file_id": "x"}])


# ---------- _parse_proof ---------------------------------------------------


def test_parse_proof_video_note() -> None:
    msg = _make_video_note_message()
    payload = _parse_proof(msg)  # type: ignore[arg-type]
    assert payload is not None
    assert payload["proof_type"] == "video_note"
    assert payload["duration_seconds"] == 5
    assert payload["chat_id"] == msg.chat_id
    assert payload["message_thread_id"] == msg.message_thread_id


def test_parse_proof_text() -> None:
    msg = _make_text_message()
    payload = _parse_proof(msg)  # type: ignore[arg-type]
    assert payload is not None
    assert payload["proof_type"] == "text"
    assert payload["text"] == "чек"


def test_parse_proof_photo() -> None:
    msg = _make_photo_message()
    payload = _parse_proof(msg)  # type: ignore[arg-type]
    assert payload is not None
    assert payload["proof_type"] == "photo"


# ---------- _text_for_code --------------------------------------------------


def test_text_for_code_forwarded() -> None:
    assert _text_for_code("forwarded") == checkin_texts.REJECT_FORWARDED


def test_text_for_code_too_short() -> None:
    assert _text_for_code("too_short") == checkin_texts.REJECT_TOO_SHORT


def test_text_for_code_wrong_topic() -> None:
    assert _text_for_code("wrong_topic") == checkin_texts.REJECT_WRONG_TOPIC
    assert _text_for_code("checkin_wrong_topic") == checkin_texts.REJECT_WRONG_TOPIC


def test_text_for_code_out_of_window() -> None:
    assert _text_for_code("out_of_window") == checkin_texts.REJECT_OUT_OF_WINDOW
    assert _text_for_code("checkin_window_closed") == checkin_texts.REJECT_OUT_OF_WINDOW


def test_text_for_code_unknown() -> None:
    assert _text_for_code("weird_thing") == checkin_texts.REJECT_UNKNOWN
    assert _text_for_code(None) == checkin_texts.REJECT_UNKNOWN


# ---------- handle_proof ---------------------------------------------------


@pytest.mark.asyncio
async def test_handle_proof_ok_true_code_none_sends_accepted() -> None:
    """Главная регрессия: ok=True с code=None → 'Принято'."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(response={"ok": True, "task_id": "abc", "code": None})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(backend.calls) == 1
    assert backend.calls[0]["path"] == "/internal/checkins/process"
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK
    assert bot.sent[0]["chat_id"] == msg.chat_id
    assert bot.sent[0]["message_thread_id"] == msg.message_thread_id
    # В топиках-форумах бот не может reply'ить на чужие сообщения —
    # Telegram вернёт "message to be replied not found". Поэтому без reply.
    assert "reply_to_message_id" not in bot.sent[0]


@pytest.mark.asyncio
async def test_handle_proof_already_exists_sends_accepted() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        response={"ok": True, "task_id": "abc", "code": "checkin_already_exists"}
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK


@pytest.mark.asyncio
async def test_handle_proof_forwarded_sends_rejection() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        response={"ok": False, "task_id": None, "code": "forwarded"}
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_FORWARDED


@pytest.mark.asyncio
async def test_handle_proof_too_short_sends_rejection() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        response={"ok": False, "task_id": None, "code": "too_short"}
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_TOO_SHORT


@pytest.mark.asyncio
async def test_handle_proof_wrong_topic_sends_rejection() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        response={"ok": False, "task_id": None, "code": "wrong_topic"}
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_WRONG_TOPIC


@pytest.mark.asyncio
async def test_handle_proof_out_of_window_sends_rejection() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        response={"ok": False, "task_id": None, "code": "out_of_window"}
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_OUT_OF_WINDOW


@pytest.mark.asyncio
async def test_handle_proof_habit_not_found_is_silent() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        response={"ok": False, "task_id": None, "code": "habit_not_found"}
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert bot.sent == []


@pytest.mark.asyncio
async def test_handle_proof_unknown_code_sends_generic_reject() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        response={"ok": False, "task_id": None, "code": "weird_future_code"}
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_UNKNOWN


@pytest.mark.asyncio
async def test_handle_proof_network_error_sends_network_fail() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(error=RuntimeError("connection refused"))

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.NETWORK_FAIL


@pytest.mark.asyncio
async def test_handle_proof_dm_is_ignored() -> None:
    """Личное сообщение (chat_id == from_user.id) — игнорируем без обращения к backend."""
    msg = FakeMessage(chat_id=100, from_user_id=100, video_note=FakeVideoNote(duration=5))
    bot = FakeBot()
    backend = FakeBackendClient(response={"ok": True})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert bot.sent == []
    assert backend.calls == []


@pytest.mark.asyncio
async def test_handle_proof_text_message_works() -> None:
    """Текстовое сообщение тоже считается proof (для привычек с proof_type=text)."""
    msg = _make_text_message()
    bot = FakeBot()
    backend = FakeBackendClient(response={"ok": True, "task_id": "x"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK
    # убедимся, что payload содержит proof_type=text
    assert backend.calls[0]["json"]["proof_type"] == "text"
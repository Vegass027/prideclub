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
    first_name: str | None = "Test"


@dataclass(slots=True)
class FakeBot:
    """Минимальный fake aiogram.Bot: записывает вызовы send_message."""
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send_message(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)


@dataclass(slots=True)
class FakeBackendClient:
    """Fake BackendClient: возвращает заранее заданный JSON или бросает.

    Для pre-filter (PR №9) есть отдельный параметр `habit_state` —
    ответ на GET /internal/bot/habit_state. Если None — бросает
    `state_error` (если задан), либо возвращает found=False.

    Метод `post` (для /internal/checkins/process) использует `response/error`.
    """

    response: dict[str, Any] | None = None
    error: Exception | None = None
    habit_state: dict[str, Any] | None = None
    state_error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    state_calls: list[dict[str, Any]] = field(default_factory=list)

    async def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"path": path, "json": json})
        if self.error is not None:
            raise self.error
        assert self.response is not None, "FakeBackendClient.response is None"
        return self.response

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"path": path, "params": params, "method": "GET"})
        return await self.get_habit_state(
            chat_id=(params or {}).get("chat_id", 0),
            user_id=(params or {}).get("user_id", 0),
        )

    async def get_habit_state(self, chat_id: int, user_id: int) -> dict[str, Any]:
        self.state_calls.append({"chat_id": chat_id, "user_id": user_id})
        if self.state_error is not None:
            raise self.state_error
        if self.habit_state is None:
            return {"found": False}
        return self.habit_state


# ---------- Helpers ---------------------------------------------------------


def _make_video_note_message() -> FakeMessage:
    return FakeMessage(video_note=FakeVideoNote(duration=5))


def _make_video_note_message_with_duration(duration: int | None) -> FakeMessage:
    """Helper: кружок с произвольной длительностью (или None для битого видео)."""
    return FakeMessage(video_note=FakeVideoNote(duration=duration))


def _make_text_message() -> FakeMessage:
    return FakeMessage(text="чек")


def _make_photo_message() -> FakeMessage:
    return FakeMessage(photo=[{"file_id": "x"}])


# Pre-filter (PR №9) default state: всё ОК — все типы разрешены, чек-ин ещё не сделан.
_DEFAULT_OK_STATE: dict[str, Any] = {
    "found": True,
    "habit_id": "any",
    "proof_types": ["video_note", "photo", "text"],
    "checkin_topic_thread_id": 12,
    "already_checked_in": False,
    "checked_in_at": None,
}


def _ok_backend(response: dict[str, Any]) -> FakeBackendClient:
    """Helper: FakeBackendClient с default OK pre-filter state.

    Все старые тесты (без pre-filter) используют это — pre-filter пропускает,
    идём по старому пути до backend.post().
    """
    return FakeBackendClient(response=response, habit_state=_DEFAULT_OK_STATE)


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
    expected = checkin_texts.REJECT_FORWARDED.format(name="Test")
    assert _text_for_code("forwarded", name="Test") == expected


def test_text_for_code_too_short() -> None:
    expected = checkin_texts.REJECT_TOO_SHORT.format(name="Test")
    assert _text_for_code("too_short", name="Test") == expected


def test_text_for_code_wrong_topic() -> None:
    expected = checkin_texts.REJECT_WRONG_TOPIC.format(name="Test")
    assert _text_for_code("wrong_topic", name="Test") == expected
    assert _text_for_code("checkin_wrong_topic", name="Test") == expected


def test_text_for_code_out_of_window() -> None:
    # Pravki-bug-fixes §Z-21 (Item 5): формат требует start/end (c window_start/end kwargs).
    expected = checkin_texts.REJECT_OUT_OF_WINDOW.format(
        name="Test", start="?", end="?",
    )
    # Без kwargs — fallback на '?'
    assert _text_for_code("out_of_window", name="Test") == expected
    assert _text_for_code("checkin_window_closed", name="Test") == expected
    # С kwargs — реальное время окна
    expected_with_times = checkin_texts.REJECT_OUT_OF_WINDOW.format(
        name="Test", start="06:00", end="12:00",
    )
    assert _text_for_code(
        "checkin_window_closed", name="Test",
        window_start="06:00", window_end="12:00",
    ) == expected_with_times


def test_text_for_code_unknown() -> None:
    assert _text_for_code("weird_thing") == checkin_texts.REJECT_UNKNOWN
    assert _text_for_code(None) == checkin_texts.REJECT_UNKNOWN


# ---------- handle_proof ---------------------------------------------------


@pytest.mark.asyncio
async def test_handle_proof_ok_true_code_none_sends_accepted() -> None:
    """Главная регрессия: ok=True с code=None → 'Принято'."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": True, "task_id": "abc", "code": None})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(backend.calls) == 1
    assert backend.calls[0]["path"] == "/internal/checkins/process"
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK.format(name="Test").format(name="Test")
    assert bot.sent[0]["chat_id"] == msg.chat_id
    assert bot.sent[0]["message_thread_id"] == msg.message_thread_id
    # В топиках-форумах бот не может reply'ить на чужие сообщения —
    # Telegram вернёт "message to be replied not found". Поэтому без reply.
    assert "reply_to_message_id" not in bot.sent[0]


@pytest.mark.asyncio
async def test_handle_proof_already_exists_sends_accepted() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": True, "task_id": "abc", "code": "checkin_already_exists"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK.format(name="Test")


@pytest.mark.asyncio
async def test_handle_proof_forwarded_sends_rejection() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": False, "task_id": None, "code": "forwarded"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_FORWARDED.format(name="Test")


@pytest.mark.asyncio
async def test_handle_proof_too_short_sends_rejection() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": False, "task_id": None, "code": "too_short"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_TOO_SHORT.format(name="Test")


@pytest.mark.asyncio
async def test_handle_proof_wrong_topic_sends_rejection() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": False, "task_id": None, "code": "wrong_topic"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_WRONG_TOPIC.format(name="Test")


@pytest.mark.asyncio
async def test_handle_proof_out_of_window_sends_rejection() -> None:
    # Pravki-bug-fixes §Z-21 (Item 5): fallback на '?' когда worker не передал times.
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": False, "task_id": None, "code": "out_of_window"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_OUT_OF_WINDOW.format(
        name="Test", start="?", end="?",
    )


@pytest.mark.asyncio
async def test_handle_proof_habit_not_found_is_silent() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": False, "task_id": None, "code": "habit_not_found"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert bot.sent == []


@pytest.mark.asyncio
async def test_handle_proof_unknown_code_sends_generic_reject() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": False, "task_id": None, "code": "weird_future_code"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.REJECT_UNKNOWN


@pytest.mark.asyncio
async def test_handle_proof_network_error_sends_network_fail() -> None:
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        error=RuntimeError("connection refused"),
        habit_state=_DEFAULT_OK_STATE,
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.NETWORK_FAIL


@pytest.mark.asyncio
async def test_handle_proof_dm_is_ignored() -> None:
    """Личное сообщение (chat_id == from_user.id) — игнорируем без обращения к backend."""
    msg = FakeMessage(chat_id=100, from_user_id=100, video_note=FakeVideoNote(duration=5))
    bot = FakeBot()
    backend = _ok_backend({"ok": True})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert bot.sent == []
    assert backend.calls == []


@pytest.mark.asyncio
async def test_handle_proof_text_message_works() -> None:
    """Текстовое сообщение тоже считается proof (для привычек с proof_type=text)."""
    msg = _make_text_message()
    bot = FakeBot()
    backend = _ok_backend({"ok": True, "task_id": "x"})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK.format(name="Test")
    # убедимся, что payload содержит proof_type=text
    assert backend.calls[0]["json"]["proof_type"] == "text"


# ---------- pre-filter (PR №9) ----------------------------------------------
# Бот должен проверять allowed_proof_types и already_checked_in ДО отправки
# в backend. Иначе worker асинхронно отвергнет задачу, а юзер уже увидит
# «Принято» (ложное срабатывание).


@pytest.mark.asyncio
async def test_prefilter_wrong_type_single_skips_backend() -> None:
    """Клуб принимает только video_note, юзер шлёт фото → отвергнуто ДО backend."""
    msg = _make_photo_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    # Backend НЕ вызван для /internal/checkins/process
    assert backend.calls == []
    # Юзер получил отказ с правильным сообщением
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "Видео-кружочек" in text or "🎥" in text


@pytest.mark.asyncio
async def test_prefilter_wrong_type_multi_skips_backend() -> None:
    """Клуб принимает [video_note, photo], юзер шлёт text → отвергнуто."""
    msg = _make_text_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note", "photo"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert backend.calls == []
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    # Список разрешённых типов
    assert "🎥" in text and "📸" in text


@pytest.mark.asyncio
async def test_prefilter_already_checked_in_skips_backend() -> None:
    """Уже отмечен сегодня → отвергнуто ДО backend, без ложного 'Принято'."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": True,
            "checked_in_at": "2026-07-23T08:00:00+00:00",
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert backend.calls == []
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "уже отметился" in text


@pytest.mark.asyncio
async def test_prefilter_habit_not_found_is_silent() -> None:
    """Клуб не привязан к чату → молчим (как habit_not_found)."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(habit_state={"found": False})

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert backend.calls == []  # ни state, ни process
    assert bot.sent == []


@pytest.mark.asyncio
async def test_prefilter_state_error_falls_back_to_backend() -> None:
    """Если /internal/bot/habit_state упал (сеть/Redis) — шлём в backend как раньше."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        state_error=RuntimeError("redis timeout"),
        response={"ok": True, "task_id": "x", "code": None},
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    # state запрос упал, но backend.process сработал
    assert len(backend.calls) == 1
    assert backend.calls[0]["path"] == "/internal/checkins/process"
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK.format(name="Test")


@pytest.mark.asyncio
async def test_prefilter_correct_type_proceeds_to_backend() -> None:
    """Разрешённый тип → pre-filter пропускает, идём дальше в backend."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note", "photo"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
        },
        response={"ok": True, "task_id": "abc"},
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    # state запрос + process
    assert len(backend.state_calls) == 1
    assert backend.state_calls[0]["chat_id"] == msg.chat_id
    assert len(backend.calls) == 1
    assert backend.calls[0]["path"] == "/internal/checkins/process"
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK.format(name="Test")


# ---------- pre-filter: video_note duration (PR №7.5) ---------------------
# Бот проверяет длительность кружка ДО отправки в backend. Worker отвергнет
# асинхронно с code=too_short, но юзер уже увидит ложное «Принято». Поэтому
# бот должен сразу сказать «кружок слишком короткий, перезапиши подлиннее».


@pytest.mark.asyncio
async def test_prefilter_video_note_too_short_skips_backend() -> None:
    """Кружок 2 сек → отвергнут ДО backend с правильным текстом."""
    msg = _make_video_note_message_with_duration(duration=2)
    bot = FakeBot()
    backend = FakeBackendClient(habit_state=_DEFAULT_OK_STATE)

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    # Backend НЕ вызван для /internal/checkins/process
    assert backend.calls == []
    # Юзер получил отказ с правильным сообщением
    assert len(bot.sent) == 1
    expected = checkin_texts.REJECT_TOO_SHORT.format(name="Test")
    assert bot.sent[0]["text"] == expected


@pytest.mark.asyncio
async def test_prefilter_video_note_boundary_3_sec_proceeds() -> None:
    """Кружок ровно 3 сек (= минимум) → проходит pre-filter, шлётся в backend."""
    msg = _make_video_note_message_with_duration(duration=3)
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state=_DEFAULT_OK_STATE,
        response={"ok": True, "task_id": "abc"},
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(backend.calls) == 1
    assert backend.calls[0]["path"] == "/internal/checkins/process"
    # payload содержит duration_seconds=3
    assert backend.calls[0]["json"]["duration_seconds"] == 3
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"] == checkin_texts.ACCEPTED_OK.format(name="Test")


@pytest.mark.asyncio
async def test_prefilter_video_note_none_duration_rejected() -> None:
    """Битый кружок (duration=None) → отвергнут ДО backend."""
    msg = _make_video_note_message_with_duration(duration=None)
    bot = FakeBot()
    backend = FakeBackendClient(habit_state=_DEFAULT_OK_STATE)

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert backend.calls == []
    assert len(bot.sent) == 1
    expected = checkin_texts.REJECT_TOO_SHORT.format(name="Test")
    assert bot.sent[0]["text"] == expected


@pytest.mark.asyncio
async def test_prefilter_video_note_too_short_takes_priority_over_wrong_type() -> None:
    """Если кружок слишком короткий И тип не в allowed — сначала про тип.

    Нет, наоборот: длительность проверяется ПОСЛЕ allowed_proof_types
    (в текущей реализации). Если тип не разрешён — отвечаем про тип,
    а не про длину. Это намеренно: чтобы юзер сразу знал, что менять.
    """
    msg = _make_video_note_message_with_duration(duration=1)
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["photo"],  # video_note не разрешён
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert backend.calls == []
    assert len(bot.sent) == 1
    # Тип не разрешён → отвечаем про тип, а не про длину
    text = bot.sent[0]["text"]
    assert "Видео-кружочек" not in text
    assert "только" in text or "Фото" in text

# ---------------------------------------------------------------------------
# Pravki-bug-fixes §Z-19: bot pre-filter для joined_late.
#
# Сценарий: бот получает habit_state с is_joined_late=True →
# 1. Отвечает REJECT_JOINED_LATE с подставленными start/end.
# 2. НЕ создаёт Celery-задачу (backend.post не вызывается).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefilter_joined_late_skips_backend() -> None:
    """is_joined_late=True → REJECT_JOINED_LATE с временем окна, без backend.post."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
            "is_joined_late": True,
            "checkin_window_start": "06:00",
            "checkin_window_end": "12:00",
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    # Главное: Celery-задача НЕ создаётся.
    process_calls = [
        c for c in backend.calls if c.get("path") == "/internal/checkins/process"
    ]
    assert process_calls == [], (
        f"Backend /internal/checkins/process must NOT be called for joined_late, "
        f"got calls: {process_calls}"
    )
    # Юзеру отвечено с подставленным временем окна.
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "06:00" in text, f"Expected start '06:00' in response, got: {text}"
    assert "12:00" in text, f"Expected end '12:00' in response, got: {text}"
    assert "вступили" in text or "завтра" in text, (
        f"Expected joined_late wording, got: {text}"
    )


@pytest.mark.asyncio
async def test_prefilter_joined_late_falls_back_on_missing_window() -> None:
    """Если is_joined_late=True но start/end отсутствуют (старый backend),
    fallback на '?' (не падаем, не молчим)."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
            "is_joined_late": True,
            # start/end отсутствуют (на случай если кто-то использует старый backend)
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    # Backend process всё равно НЕ вызван.
    process_calls = [
        c for c in backend.calls if c.get("path") == "/internal/checkins/process"
    ]
    assert process_calls == []
    # Ответ отправлен с '?' fallback.
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "?" in text, f"Expected '?' fallback in response, got: {text}"


@pytest.mark.asyncio
async def test_prefilter_joined_late_false_proceeds_to_backend() -> None:
    """is_joined_late=False → обычный путь, backend.post вызывается."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
            "is_joined_late": False,
            "checkin_window_start": "06:00",
            "checkin_window_end": "12:00",
        },
        response={"ok": True, "task_id": "t-1", "code": None},
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    # Celery-задача создаётся.
    process_calls = [
        c for c in backend.calls if c.get("path") == "/internal/checkins/process"
    ]
    assert len(process_calls) == 1, (
        f"Expected backend /internal/checkins/process to be called once, "
        f"got {len(process_calls)} calls"
    )
    # И ответ "Принято".
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "Принято" in text


# ============================================================================
# Pravki-bug-fixes §Z-21 (Item 4): caught_today prefilter
# ============================================================================


@pytest.mark.asyncio
async def test_prefilter_caught_today_status_caught_uses_REJECT_CAUGHT_TODAY() -> None:
    """status='caught' (apply_catch): caught_today=True → REJECT_CAUGHT_TODAY,
    НЕ REJECT_ALREADY_CHECKED_IN ("уже отметился" — было бы неверно).

    Тест проверяет порядок проверок: caught_today срабатывает ПЕРВЫМ,
    даже если already_checked_in=True одновременно.
    """
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": True,  # ← оба флага True одновременно
            "checked_in_at": "2026-07-23T08:00:00+00:00",
            "is_joined_late": False,
            "checkin_window_start": "06:00",
            "checkin_window_end": "12:00",
            # NEW (Item 4):
            "caught_today": True,  # ← это и есть условие для caught_today ветки
            "checkin_status": "caught",
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    # Backend не вызывается — prefilter отбил.
    process_calls = [
        c for c in backend.calls if c.get("path") == "/internal/checkins/process"
    ]
    assert process_calls == [], "Backend НЕ должен вызываться — caught_today prefilter"
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "поймали" in text, (
        f"Expected 'поймали' (REJECT_CAUGHT_TODAY), got: {text!r}"
    )
    assert "уже отметился" not in text, (
        f"Should NOT contain 'уже отметился' (REJECT_ALREADY_CHECKED_IN), got: {text!r}"
    )


@pytest.mark.asyncio
async def test_prefilter_caught_today_status_missed_uses_REJECT_PENALTY_DAY_CLOSED() -> None:
    """status='missed' (cron apply_window_expired, без кэтчера):
    caught_today=True → REJECT_PENALTY_DAY_CLOSED с нейтральным тоном.

    ВАЖНО: НЕ говорим «поймали» — никто не ловил, просто окно закрылось
    со штрафом. Различение через checkin_status.
    """
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": True,
            "checked_in_at": None,
            "is_joined_late": False,
            "checkin_window_start": "06:00",
            "checkin_window_end": "12:00",
            # cron-only: Checkin(status='missed') есть, Penalty(WINDOW_CLOSED_NO_CATCH) есть
            "caught_today": True,
            "checkin_status": "missed",
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    process_calls = [
        c for c in backend.calls if c.get("path") == "/internal/checkins/process"
    ]
    assert process_calls == [], "Backend НЕ должен вызываться"
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "поймали" not in text, (
        f"Should NOT say 'поймали' for cron-only missed, got: {text!r}"
    )
    assert "штраф" in text, (
        f"Expected 'штраф' (REJECT_PENALTY_DAY_CLOSED), got: {text!r}"
    )


@pytest.mark.asyncio
async def test_prefilter_already_checked_in_status_done_uses_REJECT_ALREADY_CHECKED_IN() -> None:
    """status='done' (юзер сам отметился): caught_today=False → пропускаем
    caught_today ветку, попадаем в already_checked_in → REJECT_ALREADY_CHECKED_IN.

    Это нормальный случай — юзер отметился, второй кружочек не нужен.
    """
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": True,
            "checked_in_at": "2026-07-23T08:00:00+00:00",
            "is_joined_late": False,
            "checkin_window_start": "06:00",
            "checkin_window_end": "12:00",
            # Нет пенальти за сегодня → caught_today=False
            "caught_today": False,
            "checkin_status": "done",
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    process_calls = [
        c for c in backend.calls if c.get("path") == "/internal/checkins/process"
    ]
    assert process_calls == [], "Backend НЕ должен вызываться"
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "уже отметился" in text


@pytest.mark.asyncio
async def test_prefilter_caught_today_takes_priority_over_already_checked_in() -> None:
    """Стресс-тест порядка: даже если ОБА флага True, caught_today
    срабатывает ПЕРВЫМ и возвращает правильный текст (не REJECT_ALREADY_CHECKED_IN).

    Это ОСНОВНОЙ тест из всего Item 4 — проверка порядка.
    Если этот тест упадёт с 'уже отметился' — значит порядок сломан.
    """
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": True,
            "checked_in_at": "2026-07-23T08:00:00+00:00",
            "is_joined_late": False,
            "checkin_window_start": "06:00",
            "checkin_window_end": "12:00",
            "caught_today": True,
            "checkin_status": "caught",
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    text = bot.sent[0]["text"]
    # Если caught_today правильно стоит ПЕРВЫМ — увидим 'поймали'.
    # Если перепутать порядок и already_checked_in сработает первым — увидим 'уже отметился'.
    assert "поймали" in text, (
        f"Order broken: caught_today должен быть ДО already_checked_in. "
        f"Got text: {text!r}"
    )


# ============================================================================
# Pravki-bug-fixes §Z-21 (Item 5): bot pre-filter для checkin_window_closed
#
# Сценарий: бот получает ответ worker'а с code='checkin_window_closed' (race
# fallback) + window_start/end → REJECT_OUT_OF_WINDOW с временем окна.
# ============================================================================


@pytest.mark.asyncio
async def test_prefilter_checkin_window_closed_passes_window_times() -> None:
    """code='checkin_window_closed' + window_start/end → REJECT_OUT_OF_WINDOW
    с реальным временем окна (НЕ статичный текст).

    Сценарий: бот pre-filter пропустил (is_within_checkin_window=True при state),
    worker ответил 200 OK с code='checkin_window_closed' (race fallback).
    Bot маппит code → текст с window_start/end.
    """
    msg = _make_video_note_message()
    bot = FakeBot()
    # Pre-filter должен пропустить (found=True, окно открыто per state),
    # worker fallback — race case.
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
            "is_joined_late": False,
            "checkin_window_start": "06:00",
            "checkin_window_end": "12:00",
        },
        response={
            "ok": False,
            "code": "checkin_window_closed",
            "message": "checkin_window_closed",
            "window_start": "06:00",
            "window_end": "12:00",
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "06:00" in text, f"Expected '06:00' in REJECT_OUT_OF_WINDOW, got: {text!r}"
    assert "12:00" in text, f"Expected '12:00' in REJECT_OUT_OF_WINDOW, got: {text!r}"
    # Текст упоминает что окно закрыто (статичная часть)
    assert "окно" in text.lower(), f"Expected 'окно' in text, got: {text!r}"


@pytest.mark.asyncio
async def test_prefilter_checkin_window_closed_falls_back_on_missing_window() -> None:
    """Если code='checkin_window_closed' без window_start/end (старый worker),
    fallback на '?' (НЕ падаем, НЕ статичный текст без плейсхолдеров)."""
    msg = _make_video_note_message()
    bot = FakeBot()
    backend = FakeBackendClient(
        habit_state={
            "found": True,
            "habit_id": "h1",
            "proof_types": ["video_note"],
            "checkin_topic_thread_id": 12,
            "already_checked_in": False,
            "checked_in_at": None,
            "is_joined_late": False,
            # start/end ОТСУТСТВУЮТ (как и в worker response ниже) — fallback на '?'
        },
        response={
            "ok": False,
            "code": "checkin_window_closed",
            "message": "checkin_window_closed",
            # window_start/window_end отсутствуют (старый worker)
        },
    )

    await handle_proof(msg, bot, backend)  # type: ignore[arg-type]

    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    # Fallback '?' вместо KeyError
    assert "?" in text, f"Expected '?' fallback, got: {text!r}"

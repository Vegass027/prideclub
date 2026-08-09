from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import Message

from bot.handlers import checkin_texts
from bot.logging_setup import get_logger
from bot.services.api_client import BackendClient

router = Router(name="checkin")
log = get_logger("bot.checkin")


# Коды успеха (запрос ушёл в Celery). На оба шлём одинаковое подтверждение.
_SUCCESS_CODES: frozenset[str] = frozenset({"ok", "checkin_already_exists"})

# Коды, при которых сообщение пользователю можно слать (не чужой чат).
_SILENT_CODES: frozenset[str] = frozenset({"habit_not_found"})


# Маппинг proof_type → (эмодзи, заголовок). Для pre-filter сообщений.
_PROOF_TYPE_DISPLAY: dict[str, tuple[str, str]] = {
    "video_note": ("🎥", "Видео-кружочек"),
    "photo": ("📸", "Фото"),
    "text": ("✍️", "Текст"),
}


def _detect_proof_type(message: Message) -> str | None:
    """aiogram Message → один из {video_note, photo, text} или None.

    None означает «неподдерживаемый тип» (стикер, документ, голосовое).
    Возвращаем None — хэндлер уже отфильтровал через F.video_note|F.photo|F.text,
    но если сюда попало что-то ещё (теоретически), вернём None.
    """
    if message.video_note:
        return "video_note"
    if message.photo:
        return "photo"
    if message.text:
        return "text"
    return None


# Минимальная длительность кружка для чек-ина. Должна совпадать с
# apps/backend/app/services/proof_validator.py:validate_proof_media
# (захардкожено как `video_note_duration < 3`). Если изменишь здесь —
# поменяй и там, иначе бот будет принимать то, что worker отвергает.
_MIN_VIDEO_NOTE_SECONDS: int = 3


def _video_note_duration(message: Message) -> int | None:
    """Длительность кружка из aiogram Message или None, если нет.

    Невалидное видео (duration=None/0) → None. Если < 3 — отвергнем
    в pre-filter. Если >= 3 — пропустим дальше.
    """
    if not message.video_note:
        return None
    duration = message.video_note.duration
    return int(duration) if duration is not None else None


def _parse_proof(message: Message) -> dict[str, Any] | None:
    """aiogram Message → Backend payload."""
    sent_at = (message.date or datetime.now(tz=UTC)).astimezone(UTC)
    base: dict[str, Any] = {
        "chat_id": message.chat.id,
        "message_thread_id": getattr(message, "message_thread_id", None),
        "message_id": message.message_id,
        "message_sent_at": sent_at.isoformat(),
    }
    if message.video_note:
        return {
            **base,
            "proof_type": "video_note",
            "duration_seconds": message.video_note.duration or 0,
        }
    if message.photo:
        return {**base, "proof_type": "photo"}
    if message.text:
        return {**base, "proof_type": "text", "text": message.text}
    return None


def _text_for_code(code: str | None, *, name: str = "", **kwargs) -> str:
    """Маппинг backend/worker code → пользовательский текст.

    `name` (first_name юзера) подставляется в шаблоны с {name}.
    Если name пустое — шаблон отрендерится без обращения, просто "{name}"
    останется плейсхолдером (намеренно: имя не всегда доступно).

    Pravki-bug-fixes §Z-19: для code='joined_late' worker возвращает
    дополнительные поля result['window_start'] / result['window_end']
    для дружественного сообщения с временем окна клуба. Передаются
    через **kwargs из handle_proof.
    """
    if code in ("forwarded",):
        return checkin_texts.REJECT_FORWARDED.format(name=name)
    if code in ("too_short",):
        return checkin_texts.REJECT_TOO_SHORT.format(name=name)
    if code in ("wrong_topic", "checkin_wrong_topic"):
        return checkin_texts.REJECT_WRONG_TOPIC.format(name=name)
    if code in ("out_of_window", "checkin_window_closed"):
        return checkin_texts.REJECT_OUT_OF_WINDOW.format(name=name)
    if code in ("joined_late",):
        return checkin_texts.REJECT_JOINED_LATE.format(
            name=name,
            start=kwargs.get("window_start", "?"),
            end=kwargs.get("window_end", "?"),
        )
    return checkin_texts.REJECT_UNKNOWN


async def _reply(
    bot: Bot,
    message: Message,
    text: str,
) -> None:
    """Шлёт ответ в ту же тему (топик) форума.

    Без reply_to_message_id: в топиках-форумах Telegram не разрешает боту
    (даже админу) reply'ить на чужие сообщения — `Bad Request: message to
    be replied not found`. Поэтому просто шлём в ту же message_thread_id.
    """
    await bot.send_message(
        chat_id=message.chat.id,
        message_thread_id=getattr(message, "message_thread_id", None),
        text=text,
    )


def _allowed_list_text(proof_types: list[str]) -> list[str]:
    """Render allowed_proof_types в строки для reject_wrong_type_multi."""
    return [
        f"{_PROOF_TYPE_DISPLAY.get(pt, ('•', pt))[0]} {_PROOF_TYPE_DISPLAY.get(pt, ('•', pt))[1]}"
        for pt in proof_types
    ]


async def _prefilter(
    backend: BackendClient,
    chat_id: int,
    user_id: int,
    detected_type: str | None,
    name: str,
    video_note_duration: int | None = None,
) -> str | None:
    """Pre-filter ДО отправки в backend.

    Возвращает:
    - None — пропустить pre-filter, шлём в backend как раньше.
    - "" — молча отказать (клуб не привязан к чату, как habit_not_found).
    - "text with {name}" — ответить пользователю и НЕ слать в backend.

    Порядок проверок (от более критичных к менее):
    1) тип в allowed_proof_types — если нет, отвечаем про тип
    2) специфичные ограничения типа: длительность кружка >= 3 сек
       (PR №7.5) — иначе worker отвергнет асинхронно с code=too_short,
       а юзер уже увидит ложное «Принято».

    Имя подставляется в шаблон.
    """
    try:
        state = await backend.get_habit_state(chat_id, user_id)
    except Exception as exc:  # noqa: BLE001 — сеть/Redis/etc., fallback
        log.warning(
            "prefilter_state_failed",
            extra={"err": str(exc), "kind": exc.__class__.__name__},
        )
        # Без state пропускаем pre-filter.
        return None

    if not state.get("found"):
        log.warning("prefilter_habit_not_found", extra={"chat_id": chat_id})
        return ""

    if state.get("already_checked_in"):
        log.info(
            "prefilter_already_checked_in",
            extra={"user_id": user_id, "chat_id": chat_id},
        )
        return checkin_texts.REJECT_ALREADY_CHECKED_IN.format(name=name)

    # Pravki-bug-fixes §Z-19 (joiner-late protection):
    # Юзер вступил в клуб сегодня после закрытия checkin_window.
    # Backend в HabitStateResponse уже посчитал is_joined_late через общий
    # habit.was_joined_after_window — бот НЕ дублирует tz-логику.
    # Возвращаем дружественное сообщение с временем окна (state['checkin_window_*']).
    # Задача в Celery НЕ создаётся — return ниже прерывает поток.
    if state.get("is_joined_late"):
        log.info(
            "prefilter_joined_late",
            extra={"user_id": user_id, "chat_id": chat_id},
        )
        return checkin_texts.REJECT_JOINED_LATE.format(
            name=name,
            start=state.get("checkin_window_start", "?"),
            end=state.get("checkin_window_end", "?"),
        )

    if detected_type is None:
        # Defense — F.video_note|F.photo|F.text уже отфильтровали.
        return checkin_texts.REJECT_UNSUPPORTED_TYPE.format(name=name)

    allowed = list(state.get("proof_types") or [])
    if detected_type not in allowed:
        if len(allowed) == 1:
            emoji, title = _PROOF_TYPE_DISPLAY.get(
                allowed[0], ("•", allowed[0])
            )
            return checkin_texts.reject_wrong_type_single(name, emoji, title)
        return checkin_texts.reject_wrong_type_multi(
            name, _allowed_list_text(allowed)
        )

    # Тип разрешён — теперь можно проверить специфичные для типа
    # ограничения. Для video_note: минимальная длительность кружка
    # (>= _MIN_VIDEO_NOTE_SECONDS) — иначе worker отвергнет асинхронно
    # с code=too_short, а юзер уже увидит ложное «Принято».
    if detected_type == "video_note":
        if video_note_duration is None or video_note_duration < _MIN_VIDEO_NOTE_SECONDS:
            log.info(
                "prefilter_video_note_too_short",
                extra={
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "duration": video_note_duration,
                },
            )
            return checkin_texts.REJECT_TOO_SHORT.format(name=name)

    # Всё ок — пропускаем.
    return None


@router.message(F.video_note | F.photo | F.text)
async def handle_proof(
    message: Message,
    bot: Bot,
    backend: BackendClient,
) -> None:
    if message.chat.id == message.from_user.id:
        return  # личка

    detected_type = _detect_proof_type(message)
    name = message.from_user.first_name or ""
    video_note_duration = _video_note_duration(message)

    # Pre-filter (PR №9): проверяем тип и дубликат ДО отправки в backend.
    # Pre-filter (PR №7.5): для video_note проверяем длительность.
    prefilter_reply = await _prefilter(
        backend=backend,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        detected_type=detected_type,
        name=name,
        video_note_duration=video_note_duration,
    )

    if prefilter_reply is not None:
        if prefilter_reply == "":
            return  # молча (habit не найден)
        await _reply(bot, message, prefilter_reply)
        return

    # Pre-filter пройден → шлём в backend как раньше.
    proof = _parse_proof(message)
    if proof is None:
        return

    payload = {
        "user_id": message.from_user.id,
        **proof,
    }

    try:
        result = await backend.post("/internal/checkins/process", payload)
    except Exception as exc:  # noqa: BLE001 — network / unexpected, не валим хэндлер
        log.error(
            "checkin_dispatch_failed",
            extra={"err": str(exc), "kind": exc.__class__.__name__},
        )
        await _reply(bot, message, checkin_texts.NETWORK_FAIL)
        return

    # Контракт: {"ok": bool, "task_id": str|None, "code": str|None}.
    # На успехе ok=True, code=None; на отказе ok=False, code="…".
    ok = result.get("ok") is True
    code = result.get("code")

    if ok or code in _SUCCESS_CODES:
        log.info(
            "checkin_accepted",
            extra={
                "user_id": message.from_user.id,
                "task_id": result.get("task_id"),
            },
        )
        await _reply(bot, message, checkin_texts.ACCEPTED_OK.format(name=name))
        return

    if code in _SILENT_CODES:
        log.warning("checkin_rejected_silent", extra={"code": code})
        return

    log.warning("checkin_rejected", extra={"code": code})
    # Pravki-bug-fixes §Z-19: для code='joined_late' worker возвращает
    # window_start/window_end в result. Передаём их в _text_for_code чтобы
    # race-fallback дал то же сообщение что и pre-filter (с временем окна).
    await _reply(
        bot,
        message,
        _text_for_code(
            code,
            name=name,
            window_start=result.get("window_start"),
            window_end=result.get("window_end"),
        ),
    )
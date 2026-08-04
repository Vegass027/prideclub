"""Сериализация Redis Stream-entry'ев в SSE-фреймы.

Шаг 4 плана `sse+redis.md`. Изолируем формат от генератора — два
места чтения (SSE-эндпоинт + потенциально REST-poll-history в v2) не
должны копипастить строку `f"id: …\\nevent: …\\ndata: …\\n\\n"`.

Wire-format (SSE spec, https://html.spec.whatwg.org/multipage/server-sent-events.html):

    id: <redis-stream-id>\\nevent: <event-name>\\ndata: <json-string>\\n\\n

NB: в payload-поле из Step 3 уже лежит готовая JSON-строка
(`event_publisher.py: json.dumps(payload)`), парсить обратно здесь
не нужно — клиент сам делает `JSON.parse(e.data)`.

Heartbeat (`Step 2 + Step 4`) — SSE-комментарий, не событие:
комментарии начинаются с `:` и не считаются клиентским EventSource
событием (нужны только для keep-alive proxy).
"""

from __future__ import annotations


def format_event_frame(
    *,
    event_id: str,
    event_name: str,
    data_json: str,
) -> str:
    """Сформировать SSE-фрейм из Redis Stream-entry.

    Args:
        event_id: Redis Stream ID (например `1785858587616-0`). Идёт в
            SSE-поле `id:`, EventSource использует как `lastEventId`
            на клиенте (см. sse+redis.md §2.4).
        event_name: Имя события (`checkin.accepted`, `checkin.rejected`).
            Идёт в SSE-поле `event:`. Фронт слушает через
            `es.addEventListener(event_name, ...)`.
        data_json: Уже-сериализованный в JSON payload (строка). Идёт в
            SSE-поле `data:`. **Передаётся as-is** — вызывающий код не
            должен передавать здесь dict.

    Returns:
        SSE-фрейм с обязательным trailing `\\n\\n` (разделитель
        сообщений в SSE spec).

    Raises:
        TypeError: `data_json` не строка (защита от типовой ошибки —
        случайно передать dict).
    """
    if not isinstance(data_json, str):
        # Защита от типовой ошибки на стороне вызывающего кода.
        # SSE-cпека требует строковое data (может быть несколько `data:`
        # строк, склеенных `\\n`, но это уже client-side concern).
        raise TypeError(
            f"data_json must be str (already-serialized JSON), got {type(data_json).__name__}"
        )
    # NB: данные уже сериализованы publisher'ом (event_publisher.py:118).
    # Здесь передаём as-is чтобы не ломать клиентский JSON.parse.
    return f"id: {event_id}\nevent: {event_name}\ndata: {data_json}\n\n"


def format_heartbeat_comment() -> str:
    """SSE-комментарий (keep-alive + пробуждение proxy).

    Формат: `: heartbeat\\n\\n`. SSE-комментарии игнорируются EventSource
    на клиенте (не доходят до `addEventListener`), но считаются байтами
    через прокси — `proxy_read_timeout` не сработает.
    """
    return ": heartbeat\n\n"


def format_connected_comment() -> str:
    """Первый chunk стрима (флаш заголовков до первого XREAD-блока).

    Формат: `: connected\\n\\n`. Идентично heartbeat-комментарию по
    синтаксису, выделено отдельной функцией для ясности в логах/тестах.
    """
    return ": connected\n\n"


__all__ = [
    "format_event_frame",
    "format_heartbeat_comment",
    "format_connected_comment",
]

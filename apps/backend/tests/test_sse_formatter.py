"""Unit-тесты для services/sse/sse_formatter.

Изолированные проверки формата SSE-фреймов. Сквозные интеграционные
тесты через генератор — в test_sse_stream_api.py (Step 4).
"""

from __future__ import annotations

import pytest

from app.services.sse.sse_formatter import (
    format_connected_comment,
    format_event_frame,
    format_heartbeat_comment,
)


class TestFormatEventFrame:
    def test_basic_three_lines(self) -> None:
        """Стандартный SSE-фрейм: `id` / `event` / `data` + разделитель."""
        result = format_event_frame(
            event_id="1785858587616-0",
            event_name="checkin.accepted",
            data_json='{"status":"done"}',
        )
        assert result == (
            "id: 1785858587616-0\n" "event: checkin.accepted\n" 'data: {"status":"done"}\n\n'
        )

    def test_data_json_passed_asis(self) -> None:
        """`data_json` уже сериализован в publisher'е — здесь без трансформации."""
        raw = '{"a": 1, "b": [1, 2, 3]}'  # уже JSON-строка из json.dumps
        result = format_event_frame(
            event_id="1-0",
            event_name="ev",
            data_json=raw,
        )
        assert raw in result
        # Никаких двойных сериализаций — нет экранирования кавычек и т.п.
        assert result == f"id: 1-0\nevent: ev\ndata: {raw}\n\n"

    def test_data_json_must_be_str(self) -> None:
        """Защита от типовой ошибки — передали dict вместо JSON-строки."""
        with pytest.raises(TypeError) as exc:
            format_event_frame(
                event_id="1-0",
                event_name="ev",
                data_json={"status": "done"},  # type: ignore[arg-type]
            )
        assert "data_json must be str" in str(exc.value)

    def test_trailing_double_newline_required_by_sse_spec(self) -> None:
        """SSE spec: фрейм заканчивается на пустую строку (\\n\\n).

        Без этого клиентский EventSource не доставит событие.
        """
        result = format_event_frame(event_id="1-0", event_name="ev", data_json="{}")
        assert result.endswith("\n\n")
        # Ровно один финальный \\n\\n, не два и не три.
        assert result.count("\n\n") == 1


class TestFormatComments:
    def test_connected_comment(self) -> None:
        assert format_connected_comment() == ": connected\n\n"

    def test_heartbeat_comment(self) -> None:
        assert format_heartbeat_comment() == ": heartbeat\n\n"

    def test_comments_start_with_colon(self) -> None:
        """SSE spec: комментарии начинаются с ':' (игнорируются клиентом)."""
        assert format_connected_comment().startswith(":")
        assert format_heartbeat_comment().startswith(":")

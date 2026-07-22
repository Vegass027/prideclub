from __future__ import annotations

import pytest

from app.core.exceptions import InvalidTopicLinkError
from app.core.telegram_links import (
    TelegramTopic,
    make_topic_link,
    parse_telegram_topic_link,
)


class TestParseTelegramTopicLink:
    def test_valid_private_supergroup(self) -> None:
        topic = parse_telegram_topic_link("https://t.me/c/4348250990/1")
        # Bot API-форма: -100 + короткий ID
        assert topic == TelegramTopic(
            chat_id=-100_000_000_0000 - 4348250990, thread_id=1
        )

    def test_valid_with_trailing_slash(self) -> None:
        topic = parse_telegram_topic_link("https://t.me/c/123/42/")
        assert topic == TelegramTopic(
            chat_id=-100_000_000_0000 - 123, thread_id=42
        )

    def test_valid_with_http(self) -> None:
        topic = parse_telegram_topic_link("http://t.me/c/555/7")
        assert topic == TelegramTopic(
            chat_id=-100_000_000_0000 - 555, thread_id=7
        )

    def test_negative_chat_id_passthrough(self) -> None:
        """Telegram chat_id супергруппы в Bot API начинается с -100."""
        raw = -1001234567890
        topic = parse_telegram_topic_link(f"https://t.me/c/{raw}/5")
        assert topic == TelegramTopic(chat_id=raw, thread_id=5)

    def test_strips_whitespace(self) -> None:
        topic = parse_telegram_topic_link("  https://t.me/c/1/2  ")
        assert topic == TelegramTopic(
            chat_id=-100_000_000_0000 - 1, thread_id=2
        )

    @pytest.mark.parametrize(
        "bad_url",
        [
            "",
            "not-a-url",
            "https://t.me/+abc",
            "https://t.me/username/123",
            "https://example.com/c/123/456",
            "https://t.me/c/abc/456",
            "https://t.me/c/123/0",
            "https://t.me/c/0/1",
        ],
    )
    def test_invalid(self, bad_url: str) -> None:
        with pytest.raises(InvalidTopicLinkError):
            parse_telegram_topic_link(bad_url)


class TestMakeTopicLink:
    def test_roundtrip_bot_api_form(self) -> None:
        url = make_topic_link(chat_id=-100_000_000_0000 - 4348250990, thread_id=1)
        assert parse_telegram_topic_link(url) == TelegramTopic(
            chat_id=-100_000_000_0000 - 4348250990, thread_id=1
        )

    def test_roundtrip_negative_id(self) -> None:
        url = make_topic_link(chat_id=-1001234567890, thread_id=5)
        assert parse_telegram_topic_link(url) == TelegramTopic(
            chat_id=-1001234567890, thread_id=5
        )
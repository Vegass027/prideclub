"""Тесты для чистых утилит app.core.utils.

Источник правды для rate-limit-парсинга (T1, дедупликация из penalty_service и
http_rate_limiter).
"""
from __future__ import annotations

import pytest

from app.core.utils import parse_rate_limit_spec


def test_parse_seconds_spec() -> None:
    assert parse_rate_limit_spec("10/10s") == (10, 10)
    assert parse_rate_limit_spec("1/1s") == (1, 1)


def test_parse_minutes_spec() -> None:
    assert parse_rate_limit_spec("5/1m") == (5, 60)
    assert parse_rate_limit_spec("3/2m") == (3, 120)


def test_parse_returns_int_tuple() -> None:
    max_n, window = parse_rate_limit_spec("10/10s")
    assert isinstance(max_n, int)
    assert isinstance(window, int)


@pytest.mark.parametrize("bad_spec", ["10", "10/", "10/10h", "10/10", "", "/10s", "abc/10s"])
def test_rejects_malformed_spec(bad_spec: str) -> None:
    with pytest.raises(ValueError):
        parse_rate_limit_spec(bad_spec)

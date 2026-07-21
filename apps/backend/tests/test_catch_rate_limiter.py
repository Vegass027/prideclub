from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.catch_rate_limiter import _parse


def test_parse_seconds() -> None:
    n, ttl = _parse("10/10s")
    assert (n, ttl) == (10, 10)


def test_parse_minutes() -> None:
    n, ttl = _parse("5/1m")
    assert (n, ttl) == (5, 60)


def test_parse_invalid() -> None:
    import pytest

    with pytest.raises(ValueError):
        _parse("10/10")
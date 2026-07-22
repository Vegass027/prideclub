"""Чистые утилиты без побочных эффектов.

Правило проекта (AGENTS.md, docs/04-code-standards.md): доменная логика живёт в
`models/`, `repositories/`, `services/`. Чистые функции парсинга/форматирования,
не зависящие от сессии/БД/Redis, — здесь.
"""
from __future__ import annotations


def parse_rate_limit_spec(spec: str) -> tuple[int, int]:
    """Парсит rate-limit спецификацию вида `10/10s` или `5/1m`.

    Возвращает кортеж `(max_count, window_seconds)`:
    - `max_count` — сколько событий допускается в окне;
    - `window_seconds` — длина окна в секундах.

    Поддерживаемые единицы:
    - `s` — секунды;
    - `m` — минуты.

    Используется в:
    - `services/http_rate_limiter.py` — для HTTP-rate-limit (`RATE_LIMIT_API_V1`,
      `RATE_LIMIT_INTERNAL`);
    - `services/penalty_service.py` — для catch-rate-limit (`RATE_LIMIT_CATCH`).

    Пример:
        >>> parse_rate_limit_spec("10/10s")
        (10, 10)
        >>> parse_rate_limit_spec("5/1m")
        (5, 60)
    """
    count, _, ttl = spec.partition("/")
    if not ttl:
        raise ValueError(f"Bad rate-limit spec: {spec!r}")
    unit = ttl[-1]
    if unit not in ("s", "m"):
        raise ValueError(f"Bad rate-limit unit in {spec!r}: expected 's' or 'm'")
    n = int(ttl[:-1])
    seconds = n * 60 if unit == "m" else n
    return int(count), seconds


__all__ = ["parse_rate_limit_spec"]

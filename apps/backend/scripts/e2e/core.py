"""HTTP-клиент + SQL-verifier для e2e-сценариев.

Использование:
    secrets = load_secrets()                     # читает /app/.env + /app/infra/.env
    http = E2EHttp(base_url=..., bot_token=..., webhook_secret=..., service_secret=...)
    db = E2EDatabase(secrets.database_url)
    async with db.session() as s:
        rows = await db.checkin_status(s, membership_id, club_date)

Секреты НЕ логируются и НЕ выводятся — сравни равенство/наличие, не echo.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse

import aiohttp

from scripts.e2e.auth import (
    FakeUser,
    generate_init_data,
    generate_service_token,
)


@dataclass(frozen=True, slots=True)
class Secrets:
    """Все секреты, необходимые сценарию.

    Не выводить в stdout/логи. Передавать только через конструкторы.

    `bot_token` — основной user-facing bot (для /api/v1/* initData).
    `bot_token_admin` — отдельный бот для /admin/v1/* (владелец
    админки идёт через него, см. middleware.py). Если BOT_TOKEN_ADMIN
    в env не задан, admin валидируется на bot_token.
    """

    bot_token: str
    bot_token_admin: str
    service_secret: str
    webhook_secret: str  # X-Telegram-Bot-Api-Secret-Token
    owner_telegram_id: int
    database_url: str
    backend_url: str  # base для HTTP-запросов (например https://api.prideclub.fun)


def load_secrets(
    *,
    backend_env: str = "/app/.env",
    compose_env: str = "/app/infra/.env",
) -> Secrets:
    """Читает секреты из /app/.env и /app/infra/.env (paths overridable).

    os.environ имеет приоритет (override). Значения в Secrets
    возвращаются «as-is», никуда не логируются.

    На проде оба файла есть и содержат нужные ключи. Локально можно
    задать ключи через os.environ (тогда файл не нужен).
    """
    env_from_files: dict[str, str] = {}

    for path in (backend_env, compose_env):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    # strip matching quotes if present
                    v = v.strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                        v = v[1:-1]
                    if k:
                        env_from_files[k] = v
        except FileNotFoundError:
            continue

    def _get(name: str) -> str:
        val = os.environ.get(name) or env_from_files.get(name, "")
        if not val:
            raise RuntimeError(
                f"required secret/env var missing: {name} "
                f"(set via os.environ or {backend_env}/{compose_env})"
            )
        return val

    return Secrets(
        bot_token=_get("BOT_TOKEN"),
        bot_token_admin=(
            os.environ.get("BOT_TOKEN_ADMIN")
            or env_from_files.get("BOT_TOKEN_ADMIN", "")
            or _get("BOT_TOKEN")  # fallback — admin endpoint при отсутствии использует основной
        ),
        service_secret=_get("SERVICE_SECRET"),
        # Pravki-subscription-2026-08-17 §Z-22 E2E: WEBHOOK_SECRET optional.
        # В backend-контейнере (habit-backend) WEBHOOK_SECRET НЕ задан
        # (только в x-bot-env для habit-bot). Сценарии, которые не вызывают
        # bot webhook (cleanup, scenario_canonical_order_prod), не нужны
        # в webhook_secret — оставляем пустую строку.
        # Сценарии, которые используют webhook_post (scenario_happy_path),
        # должны запускаться из bot-контейнера или передавать
        # WEBHOOK_SECRET через -e.
        webhook_secret=(
            os.environ.get("WEBHOOK_SECRET")
            or env_from_files.get("WEBHOOK_SECRET", "")
        ),
        owner_telegram_id=int(_get("OWNER_TELEGRAM_ID")),
        database_url=_get("DATABASE_URL"),
        backend_url=os.environ.get(
            "E2E_BACKEND_URL", env_from_files.get("E2E_BACKEND_URL", "")
        ) or "https://api.prideclub.fun",
    )


class E2EHttp:
    """Тонкий async-клиент поверх aiohttp. Секреты принимает в конструктор."""

    def __init__(
        self,
        *,
        base_url: str,
        bot_token: str,
        bot_token_admin: str | None = None,
        webhook_secret: str,
        service_secret: str,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bot_token = bot_token
        # Если bot_token_admin не передан — admin endpoints валидируются
        # на основной bot_token (matches backend behavior). Это сделано
        # чтобы простые сценарии без отдельного admin-бота работали.
        self._bot_token_admin = bot_token_admin or bot_token
        self._webhook_secret = webhook_secret
        self._service_secret = service_secret
        self._session: aiohttp.ClientSession | None = None
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def __aenter__(self) -> E2EHttp:
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @staticmethod
    def _init_data_headers(user: FakeUser, bot_token: str) -> dict[str, str]:
        return {
            "X-Telegram-Init-Data": generate_init_data(user, bot_token=bot_token),
        }

    async def admin_post(
        self,
        path: str,
        *,
        owner: FakeUser,
        json: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """POST /admin/v1/* с initData owner'а (signed admin bot token)."""
        assert self._session is not None
        url = self._base_url + path
        async with self._session.post(
            url,
            json=json,
            headers=self._init_data_headers(owner, self._bot_token_admin),
        ) as resp:
            try:
                body = await resp.json()
            except (aiohttp.ContentTypeError, ValueError):
                body = await resp.text()
            return resp.status, body

    async def admin_get(
        self,
        path: str,
        *,
        owner: FakeUser,
    ) -> tuple[int, Any]:
        assert self._session is not None
        url = self._base_url + path
        async with self._session.get(
            url,
            headers=self._init_data_headers(owner, self._bot_token_admin),
        ) as resp:
            try:
                body = await resp.json()
            except (aiohttp.ContentTypeError, ValueError):
                body = await resp.text()
            return resp.status, body

    async def api_post(
        self,
        path: str,
        *,
        user: FakeUser,
        json: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        assert self._session is not None
        url = self._base_url + path
        async with self._session.post(
            url,
            json=json,
            headers=self._init_data_headers(user, self._bot_token),
        ) as resp:
            try:
                body = await resp.json()
            except (aiohttp.ContentTypeError, ValueError):
                body = await resp.text()
            return resp.status, body

    async def api_get(
        self,
        path: str,
        *,
        user: FakeUser,
    ) -> tuple[int, Any]:
        assert self._session is not None
        url = self._base_url + path
        async with self._session.get(
            url,
            headers=self._init_data_headers(user, self._bot_token),
        ) as resp:
            try:
                body = await resp.json()
            except (aiohttp.ContentTypeError, ValueError):
                body = await resp.text()
            return resp.status, body

    async def webhook_post(
        self,
        update: dict[str, Any],
        *,
        path: str = "/bot/webhook",
    ) -> tuple[int, Any]:
        """POST /bot/webhook с X-Telegram-Bot-Api-Secret-Token.

        Бот-фронт (aiogram SimpleRequestHandler) сверяет header с
        webhook_secret через secrets.compare_digest. На 401 считаем,
        что signature не прошла — НЕ retry, fail-fast.
        """
        assert self._session is not None
        url = self._base_url + path
        headers = {
            "X-Telegram-Bot-Api-Secret-Token": self._webhook_secret,
            "Content-Type": "application/json",
        }
        async with self._session.post(url, json=update, headers=headers) as resp:
            try:
                body = await resp.json()
            except (aiohttp.ContentTypeError, ValueError):
                body = await resp.text()
            return resp.status, body

    async def internal_post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        service: str = "e2e",
    ) -> tuple[int, Any]:
        """POST /internal/* с X-Service-Token.

        Сценарий пользуется через бота (webhook), но иногда
        удобнее дёрнуть /internal напрямую (например для отладки).
        """
        assert self._session is not None
        url = self._base_url + path
        headers = {
            "X-Service-Token": generate_service_token(
                service_name=service,
                target_audience="backend-api",
                secret=self._service_secret,
                ttl_seconds=60,
            ),
        }
        async with self._session.post(url, json=json, headers=headers) as resp:
            try:
                body = await resp.json()
            except (aiohttp.ContentTypeError, ValueError):
                body = await resp.text()
            return resp.status, body


class E2EDatabase:
    """Read-only SQL-верификатор. Все запросы через asyncpg напрямую.

    Не использует SQLAlchemy — чтобы e2e не подтягивал лишний конфиг
    backend (Pydantic Settings ожидают все env'ы валидными).

    DATABASE_URL должен быть postgresql+asyncpg:// или postgresql:// —
    конвертируем в вариант для SQLAlchemy text() при необходимости.
    """

    def __init__(self, database_url: str) -> None:
        self._url = _normalize_asyncpg_url(database_url)

    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        import asyncpg

        conn = await asyncpg.connect(self._url)
        try:
            yield conn
        finally:
            await conn.close()

    # ---- query helpers ----

    async def checkin_status(
        self,
        conn: Any,
        *,
        membership_id: str,
        on_date: date,
    ) -> str | None:
        row = await conn.fetchrow(
            "SELECT status FROM checkins "
            "WHERE membership_id = $1::uuid AND date = $2",
            membership_id,
            on_date,
        )
        return row["status"] if row else None

    async def deposit_balance(self, conn: Any, *, user_id: int) -> int:
        row = await conn.fetchrow(
            "SELECT deposit_balance FROM users WHERE id = $1",
            user_id,
        )
        return int(row["deposit_balance"]) if row else 0

    async def penalty_amount_sum(
        self,
        conn: Any,
        *,
        membership_id: str,
        on_date: date,
    ) -> int:
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM penalties "
            "WHERE membership_id = $1::uuid AND date = $2",
            membership_id,
            on_date,
        )
        return int(row["s"] or 0)

    async def penalty_count(
        self,
        conn: Any,
        *,
        membership_id: str,
        on_date: date,
        reason: str | None = None,
    ) -> int:
        if reason is None:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS c FROM penalties "
                "WHERE membership_id = $1::uuid AND date = $2",
                membership_id,
                on_date,
            )
        else:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS c FROM penalties "
                "WHERE membership_id = $1::uuid AND date = $2 AND reason = $3",
                membership_id,
                on_date,
                reason,
            )
        return int(row["c"] or 0)

    async def membership_status(
        self,
        conn: Any,
        *,
        user_id: int,
        habit_id: str,
    ) -> str | None:
        row = await conn.fetchrow(
            "SELECT status::text AS status FROM memberships "
            "WHERE user_id = $1 AND habit_id = $2::uuid",
            user_id,
            habit_id,
        )
        return row["status"] if row else None

    async def user_exists(self, conn: Any, *, user_id: int) -> bool:
        row = await conn.fetchrow("SELECT 1 FROM users WHERE id = $1", user_id)
        return row is not None

    async def habit_count(self, conn: Any, *, title: str) -> int:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS c FROM habits WHERE title = $1", title
        )
        return int(row["c"] or 0)

    async def habit_meta(
        self,
        conn: Any,
        *,
        habit_id: str,
    ) -> dict[str, Any] | None:
        row = await conn.fetchrow(
            "SELECT id, title, chat_id, is_active, penalty_amount, "
            "checkin_window_start::text AS w_start, "
            "checkin_window_end::text AS w_end FROM habits WHERE id = $1::uuid",
            habit_id,
        )
        if row is None:
            return None
        return dict(row)

    async def snapshot_for_user(
        self,
        conn: Any,
        *,
        user_id: int,
        habit_id: str,
        label: str,
    ) -> dict[str, Any]:
        """Удобный дамп для финального отчёта."""
        rows = await conn.fetch(
            """
            SELECT
              u.id            AS user_id,
              u.first_name,
              u.deposit_balance,
              m.id            AS membership_id,
              m.status::text  AS membership_status,
              c.status::text  AS checkin_status,
              c.date          AS checkin_date,
              (SELECT COUNT(*) FROM penalties p
                 WHERE p.membership_id = m.id AND p.date = CURRENT_DATE) AS penalties_today,
              (SELECT COALESCE(SUM(amount),0) FROM penalties p
                 WHERE p.membership_id = m.id AND p.date = CURRENT_DATE) AS penalties_amount_today
            FROM users u
            LEFT JOIN memberships m
              ON m.user_id = u.id AND m.habit_id = $2::uuid
            LEFT JOIN checkins c
              ON c.membership_id = m.id AND c.date = CURRENT_DATE
            WHERE u.id = $1
            """,
            user_id,
            habit_id,
        )
        return {
            "label": label,
            "user_id": user_id,
            "rows": [dict(r) for r in rows],
        }


def _normalize_asyncpg_url(url: str) -> str:
    """SQLAlchemy-style postgresql+asyncpg://... → asyncpg native postgresql://..."""
    if "+asyncpg" in url:
        url = url.replace("+asyncpg", "")
    p = urlparse(url)
    # Если URL содержит query с `options=-c search_path=...` — asyncpg
    # это не разбирает. Не критично для нашего use case (одна схема public).
    return url

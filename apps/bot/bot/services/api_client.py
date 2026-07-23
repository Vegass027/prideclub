from __future__ import annotations

import asyncio
from typing import Any

import aiohttp


class BackendClient:
    """HTTP-клиент к Backend API с автоматическим X-Service-Token.

    Используется, когда бот хочет сделать запрос к /internal/*.
    """

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token_factory) -> None:
        self._session = session
        self._base_url = base_url
        self._token_factory = token_factory

    async def close(self) -> None:
        """Закрыть underlying aiohttp session. Вызывать в on_shutdown."""
        await self._session.close()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        headers = {
            "X-Service-Token": self._token_factory(),
            "Content-Type": "application/json",
        }
        async with asyncio.timeout(timeout_seconds):
            async with self._session.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                if resp.status == 204:
                    return {}
                return await resp.json()

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json=json)

    async def get_habit_state(self, chat_id: int, user_id: int) -> dict[str, Any]:
        """Состояние клуба и членства для pre-filter (PR №9).

        Бот вызывает это ДО отправки чек-ина, чтобы:
        - отвергнуть неподдерживаемый proof_type;
        - отвергнуть повторный чек-ин за сегодня.

        Endpoint: GET /internal/bot/habit_state?chat_id=...&user_id=...
        Auth: X-Service-Token (тот же секрет, что у остальных /internal/*).
        """
        return await self.get(
            "/internal/bot/habit_state",
            params={"chat_id": chat_id, "user_id": user_id},
        )
"""Register Telegram webhook for the bot.

Usage:
    docker compose exec backend python -m scripts.register_webhook
"""
from __future__ import annotations

import asyncio
import logging
import os

import aiohttp

from packages.shared.security import generate_service_token  # type: ignore[import-not-found]


async def register() -> None:
    token = os.environ["BOT_TOKEN"]
    base_url = os.environ["WEBHOOK_BASE_URL"].rstrip("/")
    path = os.environ.get("WEBHOOK_PATH", "/bot/webhook").lstrip("/")
    secret = os.environ["WEBHOOK_SECRET"]

    webhook_url = f"{base_url}/{path}"
    api_url = f"https://api.telegram.org/bot{token}/setWebhook"

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.post(
            api_url,
            json={
                "url": webhook_url,
                "secret_token": secret,
                "allowed_updates": ["message", "pre_checkout_query", "callback_query"],
                "drop_pending_updates": True,
            },
        ) as response:
            data = await response.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram rejected webhook: {data}")
            logging.getLogger(__name__).info(
                "webhook registered: url=%s ok=%s description=%s",
                webhook_url,
                data.get("ok"),
                data.get("description"),
            )


if __name__ == "__main__":
    asyncio.run(register())
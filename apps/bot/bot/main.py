from __future__ import annotations

import ipaddress
import logging
import os
import re
import sys
from urllib.parse import urlparse

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.config import Settings, get_settings
from bot.handlers import chat_member, checkin, payments, start
from bot.logging_setup import configure_logging
from bot.middlewares.rate_limit import RateLimitMiddleware
from bot.services.api_client import BackendClient

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot.main")

WEB_SERVER_HOST = "0.0.0.0"  # noqa: S104 — bind all interfaces in container
WEB_SERVER_PORT = 8080


def _validate_webhook_url(url: str, *, environment: str) -> None:
    """Fail-fast: в production webhook должен быть HTTPS на домен (не на голый IP).

    Голый IP в качестве webhook URL приводит к TLS error
    `SSL error {certificate verify failed}` от Telegram — апдейты зависают
    в pending_update_count и до бота не доходят.
    """
    if environment != "production":
        return
    if not url:
        raise RuntimeError(
            "WEBHOOK_BASE_URL is empty in production — refusing to start. "
            "Set WEBHOOK_BASE_URL=https://<your-public-domain>/<path>"
        )
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(
            f"WEBHOOK_BASE_URL must use https in production, got: {url}"
        )
    host = parsed.hostname or ""
    try:
        ipaddress.ip_address(host)
        raise RuntimeError(
            f"WEBHOOK_BASE_URL must be a public domain, not a raw IP: {url}"
        )
    except ValueError:
        pass
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host, re.IGNORECASE):
        raise RuntimeError(
            f"WEBHOOK_BASE_URL host does not look like a domain: {url}"
        )


def _make_backend_token_factory(settings: Settings):
    """Возвращает замыкание, генерирующее свежий JWT на каждый вызов.

    Без кэша: TTL токена 60 сек, кэшировать бессмысленно — после первого
    же вызова токен начинает протухать. Каждый запрос к backend получает
    свежий токен (оверхед генерации — единицы микросекунд).
    """
    from security import generate_service_token

    def _factory() -> str:
        return generate_service_token(
            service_name="bot",
            target_audience="backend-api",
            secret=settings.service_secret,
            ttl_seconds=60,
        )

    return _factory


async def on_startup(
    bot: Bot,
    settings: Settings,
    dp: Dispatcher,
    app: web.Application,
) -> None:
    """Создаём aiohttp session (нужен running loop), регистрируем BackendClient,
    потом — webhook."""
    http_session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10),
    )
    backend = BackendClient(
        session=http_session,
        base_url=settings.backend_url,
        token_factory=_make_backend_token_factory(settings),
    )
    # aiogram 3.x: кладём в workflow_data и в app["..."] для shutdown.
    dp.workflow_data["backend"] = backend
    app["backend"] = backend
    app["bot"] = bot
    log.info("backend_client_initialized")

    if settings.webhook_base_url:
        url = f"{settings.webhook_base_url.rstrip('/')}{settings.webhook_path}"
        await bot.set_webhook(
            url=url,
            secret_token=settings.webhook_secret or None,
            max_connections=40,
            allowed_updates=["message", "callback_query", "my_chat_member"],
        )
        log.info("webhook_set url=%s", url)


async def on_shutdown(app: web.Application) -> None:
    backend: BackendClient = app["backend"]
    bot: Bot = app["bot"]
    await backend.close()
    await bot.session.close()


def _trace(msg: str) -> None:
    if os.getenv("BOT_BOOT_TRACE") == "1":
        print(f"[boot] {msg}", file=sys.stderr, flush=True)


def main() -> None:
    _trace("enter_main")
    settings = get_settings()
    _trace("settings_loaded")
    configure_logging(settings.log_level)
    _trace("logging_configured")

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set")

    # Fail-fast в prod: голый IP / http в WEBHOOK_BASE_URL = SSL error в Telegram.
    _validate_webhook_url(settings.webhook_base_url, environment=settings.environment)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    _trace("bot_constructed")
    dp = Dispatcher()
    dp.message.middleware.register(RateLimitMiddleware())
    dp.include_router(start.router)
    dp.include_router(payments.router)
    dp.include_router(checkin.router)
    dp.include_router(chat_member.router)
    _trace("routers_included")

    # Long-poll fallback when no webhook URL is configured.
    if not settings.webhook_base_url:
        log.info("polling_started")
        # В polling-режиме сессию и BackendClient создаём здесь, без loop-проблем.
        import asyncio

        async def _polling_wrapper() -> None:
            http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            )
            backend = BackendClient(
                session=http_session,
                base_url=settings.backend_url,
                token_factory=_make_backend_token_factory(settings),
            )
            dp.workflow_data["backend"] = backend
            try:
                await dp.start_polling(bot)
            finally:
                await backend.close()
                await bot.session.close()

        asyncio.run(_polling_wrapper())
        return

    # Webhook mode — canonical aiogram 3.x pattern.
    app = web.Application()
    _trace("web_app_created")
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    )
    webhook_handler.register(app, path=settings.webhook_path)
    _trace("webhook_handler_registered")
    setup_application(app, dp)
    _trace("setup_application_done")

    # Startup/shutdown — отдельные корутины на уровне web.Application,
    # которые имеют доступ к running event loop.
    app.on_startup.append(lambda app: on_startup(bot, settings, dp, app))
    app.on_shutdown.append(on_shutdown)
    _trace("startup_shutdown_registered")

    log.info(
        "webhook_listen host=%s port=%d path=%s",
        WEB_SERVER_HOST,
        WEB_SERVER_PORT,
        settings.webhook_path,
    )
    _trace("calling_run_app")
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
    _trace("run_app_returned")


if __name__ == "__main__":
    main()
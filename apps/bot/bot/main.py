from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot.config import get_settings
from bot.handlers import checkin, payments, start
from bot.logging_setup import configure_logging
from bot.middlewares.rate_limit import RateLimitMiddleware

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot.main")

WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 8080


async def on_startup(bot: Bot, settings) -> None:
    """Set the Telegram webhook once the aiohttp app starts."""
    if not settings.webhook_base_url:
        return
    url = f"{settings.webhook_base_url.rstrip('/')}{settings.webhook_path}"
    await bot.set_webhook(
        url=url,
        secret_token=settings.webhook_secret or None,
        max_connections=40,
        allowed_updates=["message", "callback_query"],
    )
    log.info("webhook_set url=%s", url)


async def on_shutdown(bot: Bot) -> None:
    """Close the bot session cleanly on shutdown."""
    await bot.session.close()


def _make_on_startup(bot: Bot, settings):
    async def _on_startup() -> None:
        await on_startup(bot, settings)
    return _on_startup


def _make_on_shutdown(bot: Bot):
    async def _on_shutdown() -> None:
        await on_shutdown(bot)
    return _on_shutdown


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
    _trace("routers_included")

    # Long-poll fallback when no webhook URL is configured.
    if not settings.webhook_base_url:
        log.info("polling_started")
        try:
            asyncio.run(dp.start_polling(bot))
        finally:
            asyncio.run(bot.session.close())
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

    dp.startup.register(_make_on_startup(bot, settings))
    dp.shutdown.register(_make_on_shutdown(bot))
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

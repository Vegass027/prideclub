from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.config import get_settings

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    settings = get_settings()
    if not settings.webapp_url:
        await message.answer("Habit Club приветствует вас. Откройте Mini App.")
        return

    from aiogram.types import WebAppInfo
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    kb = InlineKeyboardBuilder()
    kb.button(text="Открыть приложение", web_app=WebAppInfo(url=settings.webapp_url))
    await message.answer(
        "Habit Club. Нажмите кнопку ниже, чтобы открыть Mini App:",
        reply_markup=kb.as_markup(),
    )
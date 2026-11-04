import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from backend.app.core.config import settings


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()


def get_mini_app_url():
    url = settings.MINING_MINI_APP_URL

    if not url.startswith("https://"):
        raise RuntimeError(
            "MINING_MINI_APP_URL must be a public HTTPS URL"
        )

    return url


@router.message(CommandStart())
async def start_handler(message: Message):
    web_app = WebAppInfo(url=get_mini_app_url())

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⛏ Open Mining Network",
                    web_app=web_app,
                )
            ]
        ]
    )

    await message.answer(
        "Welcome to AdBot Mining Network!\n\n"
        "Explore tasks, track your earnings, "
        "manage referrals and view your wallet.\n\n"
        "Participation is free. Rewards depend on "
        "eligible completed tasks and verified revenue.",
        reply_markup=keyboard,
    )


async def main():
    settings.validate_telegram_auth()
    mini_app_url = get_mini_app_url()

    bot = Bot(token=settings.BOT_TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="⛏ Mining Network",
                web_app=WebAppInfo(url=mini_app_url),
            )
        )

        logger.info("Mining Network menu button configured.")
        logger.info("Starting dedicated Mining Network bot.")

        await dispatcher.start_polling(bot)

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

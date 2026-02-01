import asyncio
import sys
import os

# Add project root to path to ensure imports work
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from aiogram import Bot, Dispatcher
from core.config import settings
from utils.logger import setup_logger
from bot.handlers import base, session, coding

logger = setup_logger()

async def main():
    logger.info("Starting OpenCode Telegram Bot...")
    
    bot = Bot(token=settings.bot_token.get_secret_value())
    dp = Dispatcher()
    
    # Include routers
    dp.include_router(base.router)
    dp.include_router(session.router)
    dp.include_router(coding.router)
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error occurred: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")

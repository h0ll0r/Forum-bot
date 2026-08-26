import asyncio
import logging
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

from config import config
from forum_monitor import ForumMonitor
from handlers import register_handlers

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
monitor = ForumMonitor(bot)


async def main():
    register_handlers(dp, monitor)
    
    # Запуск мониторинга форума в фоне
    asyncio.create_task(monitor.start_monitoring())
    
    logger.info("Бот запущен")
    await bot.send_message(config.ADMIN_ID, "✅ Бот запущен и готов к работе!")
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

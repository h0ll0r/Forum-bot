import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

load_dotenv()

from config import config
from forum_monitor import ForumMonitor
from handlers import register_handlers

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
    asyncio.create_task(monitor.start_monitoring())
    logger.info("Бот запущен")
    try:
        await bot.send_message(config.ADMIN_ID, "✅ Бот запущен и готов к работе!")
    except Exception as e:
        logger.error(f"Не удалось отправить стартовое сообщение: {e}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

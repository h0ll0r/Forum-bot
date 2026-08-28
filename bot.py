import asyncio
import logging
import os
import platform
import psutil
from datetime import datetime
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
START_TIME = datetime.now()


async def send_online_status():
    """Отправить красивый статус в канал"""
    if not config.CHANNEL_ID:
        return
    try:
        now = datetime.now()
        uptime = now - START_TIME
        uptime_str = f"{uptime.seconds // 3600}ч {(uptime.seconds % 3600) // 60}м"

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=1)

        status = "🟢 Активен" if config.is_monitoring else "🔴 Пауза"
        auth = "✅ Авторизован" if monitor.is_logged_in else "❌ Не авторизован"
        template = "✅ Настроен" if config.reply_template else "⚠️ Не настроен"

        text = (
            f"╔══════════════════════╗\n"
            f"║   🤖 БОТ В СЕТИ   ║\n"
            f"╚══════════════════════╝\n\n"
            f"📡 *Статус мониторинга:* {status}\n"
            f"🔑 *Форум:* {auth}\n"
            f"📝 *Шаблон:* {template}\n"
            f"⏱ *Интервал:* {config.check_interval} сек.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏳ *Аптайм:* {uptime_str}\n"
            f"🖥 *CPU:* {cpu}%\n"
            f"💾 *RAM:* {mem.percent}% "
            f"({mem.used // 1024 // 1024} / {mem.total // 1024 // 1024} MB)\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Статистика сессии:*\n"
            f"✅ Принято: {monitor.STATS['accepted']}\n"
            f"❌ Отклонено: {monitor.STATS['rejected']}\n"
            f"⏭ Пропущено: {monitor.STATS['skipped_taken']}\n\n"
            f"🕐 {now.strftime('%d.%m.%Y %H:%M:%S')}"
        )
        await bot.send_message(config.CHANNEL_ID, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка отправки статуса: {e}")


async def main():
    register_handlers(dp, monitor, send_online_status)
    asyncio.create_task(monitor.start_monitoring())
    logger.info("Бот запущен")

    # Уведомление админу
    try:
        await bot.send_message(config.ADMIN_ID, "✅ Бот запущен и готов к работе!")
    except Exception as e:
        logger.error(f"Не удалось отправить стартовое сообщение: {e}")

    # Статус в канал при запуске
    await send_online_status()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

import logging
import time
import psutil
import urllib.request
from datetime import datetime
from aiogram import Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import config
from forum_monitor import ForumMonitor, STATS

logger = logging.getLogger(__name__)

# ─── FSM состояния ───────────────────────────────────────────────
class LoginStates(StatesGroup):
    waiting_login = State()
    waiting_password = State()
    waiting_2fa = State()

class SettingsStates(StatesGroup):
    waiting_template = State()
    waiting_interval = State()

# ─── Клавиатуры ──────────────────────────────────────────────────
def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings"),
        ],
        [
            InlineKeyboardButton(text="▶️ Старт", callback_data="monitor_on"),
            InlineKeyboardButton(text="⏸ Пауза", callback_data="monitor_off"),
        ],
        [
            InlineKeyboardButton(text="📣 Статус в канал", callback_data="send_status"),
            InlineKeyboardButton(text="📊 Стат в канал", callback_data="send_stats"),
        ],
        [
            InlineKeyboardButton(text="🔑 Авторизация форума", callback_data="login"),
        ],
    ])

def settings_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить шаблон ответа", callback_data="set_template")],
        [InlineKeyboardButton(text="⏱ Интервал проверки", callback_data="set_interval")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])

# ─── Регистрация хендлеров ────────────────────────────────────────
def register_handlers(dp: Dispatcher, monitor: ForumMonitor, send_status_fn=None):

    # /start
    @dp.message(Command("start"))
    async def cmd_start(message: types.Message):
        if not config.is_allowed(message.from_user.id):
            return
        status = "🟢 Активен" if config.is_monitoring else "🔴 Пауза"
        auth = "✅ Авторизован" if monitor.is_logged_in else "❌ Не авторизован"
        template = "✅ Настроен" if config.reply_template else "⚠️ Не настроен"
        await message.answer(
            f"👋 Привет! Бот мониторинга форума\n\n"
            f"📡 Мониторинг: {status}\n"
            f"🔑 Форум: {auth}\n"
            f"📝 Шаблон: {template}\n"
            f"⏱ Интервал: {config.check_interval} сек.",
            reply_markup=main_keyboard()
        )

    # Callback: главное меню
    @dp.callback_query(F.data == "back_main")
    async def back_main(callback: types.CallbackQuery):
        if not config.is_allowed(callback.from_user.id):
            return
        await callback.message.edit_text("🏠 Главное меню", reply_markup=main_keyboard())

    # Callback: статистика (в боте)
    @dp.callback_query(F.data == "stats")
    async def show_stats(callback: types.CallbackQuery):
        if not config.is_allowed(callback.from_user.id):
            return
        reasons = "\n".join([f"  • {k[:40]}: {v}" for k, v in list(STATS["reject_reasons"].items())[:5]])
        text = (
            f"📊 *Статистика*\n\n"
            f"🔍 Проверено тем: {STATS['total_checked']}\n"
            f"✅ Принято: {STATS['accepted']}\n"
            f"❌ Отклонено: {STATS['rejected']}\n"
            f"⏭ Пропущено: {STATS['skipped_taken']}\n"
        )
        if reasons:
            text += f"\n*Причины отклонений:*\n{reasons}"
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=main_keyboard())

    # Callback: настройки
    @dp.callback_query(F.data == "settings")
    async def show_settings(callback: types.CallbackQuery):
        if not config.is_allowed(callback.from_user.id):
            return
        template_preview = config.reply_template[:100] + "..." if len(config.reply_template) > 100 else config.reply_template
        await callback.message.edit_text(
            f"⚙️ *Настройки*\n\n"
            f"⏱ Интервал проверки: {config.check_interval} сек.\n"
            f"📝 Шаблон:\n`{template_preview or 'Не настроен'}`",
            parse_mode="Markdown",
            reply_markup=settings_keyboard()
        )

    # Мониторинг вкл/выкл
    @dp.callback_query(F.data == "monitor_on")
    async def monitor_on(callback: types.CallbackQuery):
        if not config.is_allowed(callback.from_user.id):
            return
        if not monitor.is_logged_in:
            await callback.answer("❌ Сначала авторизуйтесь на форуме!", show_alert=True)
            return
        config.is_monitoring = True
        config.save()
        await callback.answer("▶️ Мониторинг запущен!")
        await callback.message.edit_text("▶️ Мониторинг активирован", reply_markup=main_keyboard())

    @dp.callback_query(F.data == "monitor_off")
    async def monitor_off(callback: types.CallbackQuery):
        if not config.is_allowed(callback.from_user.id):
            return
        config.is_monitoring = False
        config.save()
        await callback.answer("⏸ Мониторинг приостановлен!")
        await callback.message.edit_text("⏸ Мониторинг на паузе", reply_markup=main_keyboard())

    # ─── Авторизация ───────────────────────────────────────────────
    @dp.callback_query(F.data == "login")
    async def start_login(callback: types.CallbackQuery, state: FSMContext):
        if not config.is_allowed(callback.from_user.id):
            return
        await state.set_state(LoginStates.waiting_login)
        await callback.message.answer("🔑 Введи логин от форума:")

    @dp.message(LoginStates.waiting_login)
    async def get_login(message: types.Message, state: FSMContext):
        if not config.is_allowed(message.from_user.id):
            return
        await state.update_data(login=message.text)
        await state.set_state(LoginStates.waiting_password)
        await message.answer("🔒 Введи пароль:")

    @dp.message(LoginStates.waiting_password)
    async def get_password(message: types.Message, state: FSMContext):
        if not config.is_allowed(message.from_user.id):
            return
        await state.update_data(password=message.text)
        data = await state.get_data()
        try:
            await message.delete()
        except:
            pass
        msg = await message.answer("⏳ Выполняю вход...")
        result = await monitor.login(data["login"], data["password"])
        if result == "2fa_required":
            await state.set_state(LoginStates.waiting_2fa)
            await msg.edit_text("📱 Требуется код из Google Authenticator.\nВведи 6-значный код:")
        elif result == "success":
            await state.clear()
            await msg.edit_text("✅ Авторизация успешна! Мониторинг можно запускать.")
        else:
            await state.clear()
            await msg.edit_text("❌ Ошибка входа. Проверь логин/пароль и попробуй снова через /start")

    @dp.message(LoginStates.waiting_2fa)
    async def get_2fa(message: types.Message, state: FSMContext):
        if not config.is_allowed(message.from_user.id):
            return
        result = await monitor.submit_2fa(message.text.strip())
        if result:
            await state.clear()
            await message.answer("✅ Авторизация успешна! Мониторинг можно запускать.")
        else:
            await message.answer("❌ Неверный код. Вводи код сразу как он появился в приложении:")

    # ─── Шаблон ответа ─────────────────────────────────────────────
    @dp.callback_query(F.data == "set_template")
    async def ask_template(callback: types.CallbackQuery, state: FSMContext):
        if not config.is_allowed(callback.from_user.id):
            return
        current = config.reply_template or "не задан"
        await state.set_state(SettingsStates.waiting_template)
        await callback.message.answer(
            f"📝 Текущий шаблон:\n\n`{current}`\n\n"
            f"Введи новый шаблон ответа (или /cancel для отмены):",
            parse_mode="Markdown"
        )

    @dp.message(SettingsStates.waiting_template)
    async def save_template(message: types.Message, state: FSMContext):
        if not config.is_allowed(message.from_user.id):
            return
        if message.text == "/cancel":
            await state.clear()
            await message.answer("Отменено.")
            return
        config.reply_template = message.text
        config.save()
        await state.clear()
        await message.answer("✅ Шаблон сохранён!", reply_markup=main_keyboard())

    # ─── Интервал проверки ─────────────────────────────────────────
    @dp.callback_query(F.data == "set_interval")
    async def ask_interval(callback: types.CallbackQuery, state: FSMContext):
        if not config.is_allowed(callback.from_user.id):
            return
        await state.set_state(SettingsStates.waiting_interval)
        await callback.message.answer(
            f"⏱ Текущий интервал: {config.check_interval} сек.\n\n"
            f"Введи новый интервал в секундах (минимум 60):"
        )

    @dp.message(SettingsStates.waiting_interval)
    async def save_interval(message: types.Message, state: FSMContext):
        if not config.is_allowed(message.from_user.id):
            return
        try:
            val = int(message.text.strip())
            if val < 60:
                await message.answer("❌ Минимальный интервал — 60 секунд")
                return
            config.check_interval = val
            config.save()
            await state.clear()
            await message.answer(f"✅ Интервал установлен: {val} сек.", reply_markup=main_keyboard())
        except ValueError:
            await message.answer("❌ Введи число (например: 120)")

    # ─── Статус в канал ────────────────────────────────────────────
    @dp.callback_query(F.data == "send_status")
    async def send_status(callback: types.CallbackQuery):
        if not config.is_allowed(callback.from_user.id):
            return
        if send_status_fn:
            await send_status_fn()
            await callback.answer("✅ Статус отправлен в канал!")
        else:
            await callback.answer("❌ Функция статуса недоступна")

    # ─── Статистика в канал ────────────────────────────────────────
    @dp.callback_query(F.data == "send_stats")
    async def send_stats_to_channel(callback: types.CallbackQuery):
        if not config.is_allowed(callback.from_user.id):
            return
        if not config.CHANNEL_ID:
            await callback.answer("❌ CHANNEL_ID не настроен!", show_alert=True)
            return
        try:
            now = datetime.now()

            # Аптайм
            try:
                from bot import START_TIME
                uptime = now - START_TIME
                h, m = uptime.seconds // 3600, (uptime.seconds % 3600) // 60
                uptime_str = f"{h}ч {m}м"
            except:
                uptime_str = "—"

            # Пинг до форума
            try:
                t0 = time.monotonic()
                urllib.request.urlopen("https://forum.majestic-rp.ru", timeout=5)
                ping_str = f"{int((time.monotonic() - t0) * 1000)} мс"
            except:
                ping_str = "недоступен"

            # Система
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0.5)
            status = "🟢 Активен" if config.is_monitoring else "🔴 Пауза"

            text = (
                f"🤖 *Majestic RP · Статус бота*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📡 Мониторинг: {status}\n"
                f"⏳ Аптайм: `{uptime_str}`\n"
                f"🏓 Пинг до форума: `{ping_str}`\n"
                f"🖥 CPU `{cpu}%`  ·  💾 RAM `{mem.percent}%`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 *Статистика жалоб*\n"
                f"✅ Принято: *{STATS['accepted']}*\n"
                f"❌ Отклонено: *{STATS['rejected']}*\n"
                f"⏭ Пропущено: *{STATS['skipped_taken']}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🕐 `{now.strftime('%d.%m.%Y %H:%M:%S')}`"
            )
            await callback.bot.send_message(config.CHANNEL_ID, text, parse_mode="Markdown")
            await callback.answer("✅ Отправлено в канал!")
        except Exception as e:
            logger.error(f"Ошибка отправки статистики: {e}")
            await callback.answer("❌ Ошибка отправки", show_alert=True)

    # /cancel глобальный
    @dp.message(Command("cancel"))
    async def cancel(message: types.Message, state: FSMContext):
        if not config.is_allowed(message.from_user.id):
            return
        await state.clear()
        await message.answer("Отменено.", reply_markup=main_keyboard())

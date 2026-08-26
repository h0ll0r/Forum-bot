import asyncio
import logging
import re
import json
import os
from typing import Optional, Dict, List

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config import config

logger = logging.getLogger(__name__)

SESSION_FILE = "forum_session.json"

ALLOWED_VIDEO_PLATFORMS = [
    "youtube.com", "youtu.be", "twitch.tv", "trovo.live",
    "rutube.ru", "drive.google.com", "imgur.com",
    "disk.yandex.ru", "yadi.sk", "vkvideo.ru", "vk.com/video"
]
ALLOWED_IMAGE_PLATFORMS = [
    "imgur.com", "yapx.ru", "prnt.sc", "drive.google.com",
    "disk.yandex.ru", "yadi.sk", "fotora.ru"
]

STATS = {
    "total_checked": 0,
    "accepted": 0,
    "rejected": 0,
    "skipped_taken": 0,
    "reject_reasons": {}
}


class ForumMonitor:
    def __init__(self, bot):
        self.bot = bot
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.is_logged_in = False

    async def _init_browser(self):
        """Инициализировать браузер"""
        if self.playwright is None:
            self.playwright = await async_playwright().start()
        if self.browser is None or not self.browser.is_connected():
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )

    async def _get_context(self) -> BrowserContext:
        """Получить контекст браузера (с сохранённой сессией если есть)"""
        await self._init_browser()
        if self.context:
            return self.context

        storage = {}
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "r") as f:
                storage = json.load(f)

        self.context = await self.browser.new_context(
            storage_state=storage if storage else None,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        return self.context

    async def _save_session(self):
        """Сохранить сессию"""
        if self.context:
            storage = await self.context.storage_state()
            with open(SESSION_FILE, "w") as f:
                json.dump(storage, f)
            logger.info("Сессия сохранена")

    async def login(self, username: str, password: str) -> str:
        """Вход на форум через браузер"""
        try:
            ctx = await self._get_context()
            page = await ctx.new_page()

            logger.info("Открываю страницу логина...")
            await page.goto("https://forum.majestic-rp.ru/login/", wait_until="networkidle", timeout=30000)

            # Ждём пока JS защита пройдёт
            await page.wait_for_selector("input[name='login']", timeout=15000)

            await page.fill("input[name='login']", username)
            await page.fill("input[name='password']", password)

            # Чекбокс "запомнить"
            try:
                await page.check("input[name='remember']")
            except:
                pass

            await page.click("button[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15000)

            url = page.url
            logger.info(f"После логина URL: {url}")

            # Проверяем 2FA
            if "two-step" in url or "two_step" in url:
                await page.close()
                return "2fa_required"

            # Проверяем успех
            content = await page.content()
            if "logout" in content.lower() or "выйти" in content.lower():
                await self._save_session()
                self.is_logged_in = True
                await page.close()
                logger.info("Успешный вход!")
                return "success"

            # Может быть 2FA по содержимому
            if "two_step" in content or "totp" in content:
                await page.close()
                return "2fa_required"

            await page.close()
            logger.warning("Вход не удался")
            return "failed"

        except Exception as e:
            logger.error(f"Ошибка входа: {e}")
            return "failed"

    async def submit_2fa(self, totp_code: str) -> bool:
        """Отправить 2FA код"""
        try:
            ctx = await self._get_context()
            page = await ctx.new_page()

            await page.goto("https://forum.majestic-rp.ru/login/two-step", wait_until="networkidle", timeout=20000)

            # Вводим код
            await page.fill("input[name='code']", totp_code)

            # Чекбокс доверять устройству
            try:
                await page.check("input[name='trust']")
            except:
                pass

            await page.click("button[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15000)

            content = await page.content()
            if "logout" in content.lower() or "выйти" in content.lower():
                await self._save_session()
                self.is_logged_in = True
                await page.close()
                return True

            await page.close()
            return False

        except Exception as e:
            logger.error(f"Ошибка 2FA: {e}")
            return False

    async def check_session(self) -> bool:
        """Проверить что сессия жива"""
        try:
            ctx = await self._get_context()
            page = await ctx.new_page()
            await page.goto("https://forum.majestic-rp.ru/", wait_until="networkidle", timeout=20000)
            content = await page.content()
            await page.close()
            return "logout" in content.lower() or "выйти" in content.lower()
        except:
            return False

    async def get_new_threads(self) -> List[Dict]:
        """Получить список новых тем"""
        try:
            ctx = await self._get_context()
            page = await ctx.new_page()
            await page.goto(config.FORUM_URL, wait_until="networkidle", timeout=20000)
            content = await page.content()
            await page.close()

            soup = BeautifulSoup(content, "html.parser")
            threads = []

            for item in soup.select("div.structItem--thread"):
                title_el = item.select_one("div.structItem-title a[data-tp-primary]")
                if not title_el:
                    title_el = item.select_one("div.structItem-title a")
                if not title_el:
                    continue

                href = title_el.get("href", "")
                thread_url = "https://forum.majestic-rp.ru" + href
                thread_id = re.search(r'\.(\d+)/?$', thread_url)
                if not thread_id:
                    continue
                thread_id = thread_id.group(1)

                if thread_id in config.processed_threads:
                    continue

                threads.append({
                    "id": thread_id,
                    "title": title_el.text.strip(),
                    "url": thread_url,
                })

            return threads
        except Exception as e:
            logger.error(f"Ошибка получения тем: {e}")
            return []

    async def get_thread_content(self, url: str) -> Optional[Dict]:
        """Получить содержимое темы"""
        try:
            ctx = await self._get_context()
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=20000)
            content = await page.content()
            await page.close()

            soup = BeautifulSoup(content, "html.parser")

            first_post = soup.select_one("article.message--post")
            if not first_post:
                return None

            body = first_post.select_one("div.bbWrapper")
            content_text = body.get_text("\n", strip=True) if body else ""

            all_posts = soup.select("article.message--post")
            replies = []
            for post in all_posts[1:]:
                author_el = post.select_one("a.username")
                if author_el:
                    replies.append(author_el.text.strip())

            # XenForo thread ID
            xf_id_match = re.search(r'/threads/[^/]+-(\d+)/', url)
            if not xf_id_match:
                xf_id_match = re.search(r'\.(\d+)/?', url)
            xf_thread_id = xf_id_match.group(1) if xf_id_match else None

            return {
                "content": content_text,
                "replies": replies,
                "xf_thread_id": xf_thread_id,
            }
        except Exception as e:
            logger.error(f"Ошибка чтения темы: {e}")
            return None

    async def post_reply(self, url: str, message: str, xf_thread_id: str) -> bool:
        """Ответить в теме"""
        try:
            ctx = await self._get_context()
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=20000)

            # Заполнить текстовое поле ответа
            await page.wait_for_selector(".js-editorHiddenVal, textarea[name='message']", timeout=10000)

            try:
                # XenForo rich editor
                editor = page.locator(".fr-element[contenteditable='true']")
                await editor.click()
                await editor.fill(message)
            except:
                # Fallback: обычный textarea
                await page.fill("textarea[name='message']", message)

            await page.click("button.button--primary[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.close()
            return True
        except Exception as e:
            logger.error(f"Ошибка ответа: {e}")
            return False

    async def close_thread(self, url: str, xf_thread_id: str) -> bool:
        """Закрыть тему"""
        try:
            ctx = await self._get_context()
            page = await ctx.new_page()

            # Открыть меню управления темой
            await page.goto(url, wait_until="networkidle", timeout=20000)

            # XenForo: кнопка закрытия темы для модераторов
            try:
                await page.click("a[data-xf-click='overlay'][href*='toggle-open']", timeout=5000)
                await page.wait_for_load_state("networkidle", timeout=10000)
            except:
                # Альтернатив: через inline mod
                try:
                    close_url = f"https://forum.majestic-rp.ru/threads/{xf_thread_id}/toggle-open"
                    await page.goto(close_url, wait_until="networkidle", timeout=15000)
                except:
                    pass

            await page.close()
            return True
        except Exception as e:
            logger.error(f"Ошибка закрытия темы: {e}")
            return False

    def validate_thread(self, content: str, title: str) -> Dict:
        """Проверить жалобу по правилам"""
        errors = []

        required_fields = [
            "Ваш игровой никнейм",
            "Ваш статический ID",
            "Статический #ID нарушителя",
            "Дата и время нарушения",
            "Краткое описание ситуации",
            "Доказательства",
        ]
        for field in required_fields:
            idx = content.find(field)
            if idx == -1:
                errors.append(f"❌ Поле «{field}» отсутствует (Правило 1)")
                continue
            after = content[idx + len(field):idx + len(field) + 200].strip()
            if not after or after.startswith("ㅤ") or len(after) < 2:
                errors.append(f"❌ Поле «{field}» не заполнено (Правило 1)")

        urls_in_content = re.findall(r'https?://[^\s\]>\"\']+', content)
        has_valid_proof = any(
            any(p in url for p in ALLOWED_VIDEO_PLATFORMS + ALLOWED_IMAGE_PLATFORMS)
            for url in urls_in_content
        )

        if urls_in_content and not has_valid_proof:
            errors.append("❌ Доказательства загружены не на разрешённую платформу (Правило 10/10.1)")
        if not urls_in_content:
            errors.append("❌ Доказательства не предоставлены (Правило 4)")

        serious_keywords = ["deathmatch", " dm ", " db ", "pg", "nonrp", "non-rp", "оскорбление"]
        is_serious = any(kw in content.lower() or kw in title.lower() for kw in serious_keywords)
        if is_serious:
            has_video = any(p in content for p in ["youtube.com", "youtu.be", "twitch.tv", "rutube.ru", "trovo.live", "vkvideo.ru"])
            if not has_video:
                errors.append("❌ Для данного нарушения требуется видеозапись (Правило 4)")

        return {"valid": len(errors) == 0, "errors": errors}

    async def process_thread(self, thread: Dict) -> str:
        """Обработать одну тему"""
        url = thread["url"]
        thread_id = thread["id"]
        title = thread["title"]

        STATS["total_checked"] += 1
        data = await self.get_thread_content(url)
        if not data:
            return "error"

        if data["replies"]:
            taken_by = data["replies"][0]
            STATS["skipped_taken"] += 1
            await self.notify(f"⚠️ Тема уже взята\n📌 [{title}]({url})\n👤 Взял: {taken_by}")
            config.processed_threads.append(thread_id)
            config.save()
            return "taken"

        validation = self.validate_thread(data["content"], title)

        if not config.reply_template:
            logger.warning("Шаблон ответа не настроен")
            return "no_template"

        if validation["valid"]:
            reply_text = config.reply_template
            STATS["accepted"] += 1
            status_emoji, status_text = "✅", "Принята"
        else:
            errors_text = "\n".join(validation["errors"])
            reply_text = f"Жалоба отклонена по следующим причинам:\n\n{errors_text}\n\nПожалуйста, исправьте ошибки и подайте жалобу заново."
            STATS["rejected"] += 1
            for err in validation["errors"]:
                key = err[:50]
                STATS["reject_reasons"][key] = STATS["reject_reasons"].get(key, 0) + 1
            status_emoji, status_text = "❌", "Отклонена"

        replied = await self.post_reply(url, reply_text, data["xf_thread_id"])
        if replied:
            await self.close_thread(url, data["xf_thread_id"])

        errors_preview = "\n".join(validation["errors"][:3]) if not validation["valid"] else ""
        await self.notify(f"{status_emoji} Жалоба {status_text}\n📌 [{title}]({url})\n{errors_preview}")

        config.processed_threads.append(thread_id)
        config.save()
        return "processed"

    async def notify(self, text: str):
        try:
            if config.CHANNEL_ID:
                await self.bot.send_message(config.CHANNEL_ID, text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка уведомления: {e}")

    async def start_monitoring(self):
        """Основной цикл мониторинга"""
        logger.info("Мониторинг запущен")
        check_count = 0
        while True:
            if config.is_monitoring and self.is_logged_in:
                try:
                    # Проверяем сессию каждые 30 итераций
                    if check_count % 30 == 0:
                        session_ok = await self.check_session()
                        if not session_ok:
                            self.is_logged_in = False
                            await self.bot.send_message(
                                config.ADMIN_ID,
                                "⚠️ Сессия форума истекла! Нужна повторная авторизация.\nНажми /start"
                            )
                            await asyncio.sleep(60)
                            continue

                    threads = await self.get_new_threads()
                    if threads:
                        logger.info(f"Найдено новых тем: {len(threads)}")
                    for thread in threads:
                        await self.process_thread(thread)
                        await asyncio.sleep(5)

                    check_count += 1
                except Exception as e:
                    logger.error(f"Ошибка мониторинга: {e}")

            await asyncio.sleep(config.check_interval)

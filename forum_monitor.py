import asyncio
import logging
import re
import pickle
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import httpx
from bs4 import BeautifulSoup

from config import config

logger = logging.getLogger(__name__)

SESSION_FILE = "forum_session.pkl"

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
        self.client: Optional[httpx.AsyncClient] = None
        self.is_logged_in = False
        self._load_session()

    def _load_session(self):
        """Загрузить сохранённую сессию"""
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, "rb") as f:
                cookies = pickle.load(f)
            self.client = httpx.AsyncClient(
                cookies=cookies,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                follow_redirects=True,
                timeout=30
            )
            self.is_logged_in = True
            logger.info("Сессия загружена из файла")
        else:
            self.client = httpx.AsyncClient(
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                follow_redirects=True,
                timeout=30
            )
            self.is_logged_in = False

    def _save_session(self):
        with open(SESSION_FILE, "wb") as f:
            pickle.dump(dict(self.client.cookies), f)
        logger.info("Сессия сохранена")

    async def login(self, username: str, password: str, totp_code: str = "") -> bool:
        """Вход на форум"""
        try:
            # Получить страницу логина для csrf токена
            r = await self.client.get("https://forum.majestic-rp.ru/login/")
            soup = BeautifulSoup(r.text, "html.parser")

            csrf_input = soup.find("input", {"name": "_xfToken"})
            csrf_token = csrf_input["value"] if csrf_input else ""

            payload = {
                "_xfToken": csrf_token,
                "login": username,
                "password": password,
                "remember": "1",
                "_xfRedirect": "https://forum.majestic-rp.ru/",
            }
            if totp_code:
                payload["totp_code"] = totp_code

            r = await self.client.post(
                "https://forum.majestic-rp.ru/login/login",
                data=payload
            )

            # Проверяем успешность входа
            if "logout" in r.text.lower() or "выйти" in r.text.lower():
                self._save_session()
                self.is_logged_in = True
                logger.info("Успешный вход на форум")
                return True

            # Возможно нужен 2FA
            if "two_step" in r.url.path or "two-step" in r.url.path or "totp" in r.text.lower():
                return "2fa_required"

            logger.warning("Вход не удался")
            return False

        except Exception as e:
            logger.error(f"Ошибка входа: {e}")
            return False

    async def submit_2fa(self, totp_code: str) -> bool:
        """Отправить 2FA код"""
        try:
            r = await self.client.get("https://forum.majestic-rp.ru/login/two-step")
            soup = BeautifulSoup(r.text, "html.parser")
            csrf_input = soup.find("input", {"name": "_xfToken"})
            csrf_token = csrf_input["value"] if csrf_input else ""

            payload = {
                "_xfToken": csrf_token,
                "code": totp_code,
                "provider": "totp",
                "remember": "1",
                "trust": "1",
                "_xfRedirect": "https://forum.majestic-rp.ru/",
            }
            r = await self.client.post(
                "https://forum.majestic-rp.ru/login/two-step",
                data=payload
            )
            if "logout" in r.text.lower() or "выйти" in r.text.lower():
                self._save_session()
                self.is_logged_in = True
                return True
            return False
        except Exception as e:
            logger.error(f"Ошибка 2FA: {e}")
            return False

    async def check_session(self) -> bool:
        """Проверить что сессия ещё живая"""
        try:
            r = await self.client.get("https://forum.majestic-rp.ru/")
            return "logout" in r.text.lower() or "выйти" in r.text.lower()
        except:
            return False

    async def get_new_threads(self) -> List[Dict]:
        """Получить список новых тем в разделе"""
        try:
            r = await self.client.get(config.FORUM_URL)
            soup = BeautifulSoup(r.text, "html.parser")

            threads = []
            # XenForo структура тем
            for item in soup.select("div.structItem--thread"):
                title_el = item.select_one("div.structItem-title a[data-tp-primary]")
                if not title_el:
                    title_el = item.select_one("div.structItem-title a")
                if not title_el:
                    continue

                thread_url = "https://forum.majestic-rp.ru" + title_el["href"]
                thread_id = re.search(r'\.(\d+)/?$', thread_url)
                if not thread_id:
                    continue
                thread_id = thread_id.group(1)

                if thread_id in config.processed_threads:
                    continue

                # Проверяем счётчик ответов
                replies_el = item.select_one("dl.pairs--justified dt")
                # Дата
                date_el = item.select_one("time")
                date_str = date_el["datetime"] if date_el else ""

                threads.append({
                    "id": thread_id,
                    "title": title_el.text.strip(),
                    "url": thread_url,
                    "date": date_str,
                })

            return threads
        except Exception as e:
            logger.error(f"Ошибка получения тем: {e}")
            return []

    async def get_thread_content(self, url: str) -> Optional[Dict]:
        """Получить содержимое темы и список ответов"""
        try:
            r = await self.client.get(url)
            soup = BeautifulSoup(r.text, "html.parser")

            # Первый пост
            first_post = soup.select_one("article.message--post")
            if not first_post:
                return None

            content = first_post.select_one("div.bbWrapper")
            content_text = content.get_text("\n", strip=True) if content else ""

            # Все ответы
            all_posts = soup.select("article.message--post")
            replies = []
            for post in all_posts[1:]:  # пропускаем первый (сам вопрос)
                author_el = post.select_one("a.username")
                author = author_el.text.strip() if author_el else "Unknown"
                replies.append(author)

            # CSRF токен для ответа
            csrf_input = soup.find("input", {"name": "_xfToken"})
            csrf_token = csrf_input["value"] if csrf_input else ""

            # ID темы для XenForo
            thread_id_match = re.search(r'thread-(\d+)', r.text)
            xf_thread_id = thread_id_match.group(1) if thread_id_match else None

            return {
                "content": content_text,
                "replies": replies,
                "csrf_token": csrf_token,
                "xf_thread_id": xf_thread_id,
                "html": r.text
            }
        except Exception as e:
            logger.error(f"Ошибка чтения темы: {e}")
            return None

    def validate_thread(self, content: str, title: str) -> Dict:
        """Проверить жалобу по правилам"""
        errors = []

        # 1. Проверка обязательных полей
        required_fields = [
            "Ваш игровой никнейм",
            "Ваш статический ID",
            "Статический #ID нарушителя",
            "Дата и время нарушения",
            "Краткое описание ситуации",
            "Доказательства",
        ]
        for field in required_fields:
            # Ищем поле и проверяем что после него есть текст
            pattern = re.compile(re.escape(field) + r'[\s\S]{0,20}?[\n\r]([\s\S]+?)(?=Ваш|Статический|Дата|Краткое|Доказательства|$)', re.IGNORECASE)
            match = pattern.search(content)
            if not match or not match.group(1).strip() or match.group(1).strip() == "ㅤ":
                errors.append(f"❌ Поле «{field}» не заполнено (Правило 1)")

        # 2. Проверка доказательств на разрешённые платформы
        urls_in_content = re.findall(r'https?://[^\s\]>\"\']+', content)
        has_valid_proof = False
        has_any_url = bool(urls_in_content)

        for url in urls_in_content:
            if any(p in url for p in ALLOWED_VIDEO_PLATFORMS + ALLOWED_IMAGE_PLATFORMS):
                has_valid_proof = True
                break

        if has_any_url and not has_valid_proof:
            errors.append("❌ Доказательства загружены не на разрешённую платформу (Правило 10/10.1)")

        if not has_any_url:
            errors.append("❌ Доказательства не предоставлены (Правило 4)")

        # 3. Проверка на видео для серьёзных нарушений
        serious_keywords = ["deathmatch", "dm", "db", "pg", "nonrp", "non-rp", "оскорбление"]
        is_serious = any(kw in content.lower() or kw in title.lower() for kw in serious_keywords)
        if is_serious:
            has_video = any(p in content for p in ["youtube.com", "youtu.be", "twitch.tv", "rutube.ru", "trovo.live", "vkvideo.ru"])
            if not has_video:
                errors.append("❌ Для данного типа нарушения требуется видеозапись (Правило 4)")

        return {
            "valid": len(errors) == 0,
            "errors": errors
        }

    async def post_reply(self, thread_url: str, message: str, csrf_token: str, xf_thread_id: str) -> bool:
        """Оставить ответ в теме"""
        try:
            payload = {
                "_xfToken": csrf_token,
                "message": message,
                "_xfWithData": "1",
                "last_date": "0",
                "last_known_date": "0",
            }
            post_url = f"https://forum.majestic-rp.ru/threads/{xf_thread_id}/add-reply"
            r = await self.client.post(post_url, data=payload)
            return r.status_code == 200 and ("error" not in r.text.lower() or "message" in r.text.lower())
        except Exception as e:
            logger.error(f"Ошибка ответа: {e}")
            return False

    async def close_thread(self, thread_url: str, csrf_token: str, xf_thread_id: str) -> bool:
        """Закрыть тему"""
        try:
            payload = {
                "_xfToken": csrf_token,
                "discussion_open": "0",
            }
            r = await self.client.post(
                f"https://forum.majestic-rp.ru/threads/{xf_thread_id}/edit",
                data=payload
            )
            return r.status_code in [200, 303]
        except Exception as e:
            logger.error(f"Ошибка закрытия темы: {e}")
            return False

    async def process_thread(self, thread: Dict) -> str:
        """Обработать одну тему"""
        url = thread["url"]
        thread_id = thread["id"]
        title = thread["title"]

        STATS["total_checked"] += 1

        data = await get_thread_content_safe(self, url)
        if not data:
            return "error"

        # Проверить — взята ли тема другим администратором
        if data["replies"]:
            taken_by = data["replies"][0]
            STATS["skipped_taken"] += 1
            await self.notify(
                f"⚠️ Тема уже взята\n"
                f"📌 [{title}]({url})\n"
                f"👤 Взял: {taken_by}"
            )
            config.processed_threads.append(thread_id)
            config.save()
            return "taken"

        # Валидация жалобы
        validation = self.validate_thread(data["content"], title)

        if not config.reply_template:
            logger.warning("Шаблон ответа не настроен, пропускаем")
            return "no_template"

        if validation["valid"]:
            reply_text = config.reply_template
            STATS["accepted"] += 1
            status_emoji = "✅"
            status_text = "Принята"
        else:
            errors_text = "\n".join(validation["errors"])
            reply_text = (
                f"Жалоба отклонена по следующим причинам:\n\n"
                f"{errors_text}\n\n"
                f"Пожалуйста, исправьте ошибки и подайте жалобу заново."
            )
            STATS["rejected"] += 1
            for err in validation["errors"]:
                key = err[:50]
                STATS["reject_reasons"][key] = STATS["reject_reasons"].get(key, 0) + 1
            status_emoji = "❌"
            status_text = "Отклонена"

        # Ответить
        replied = await self.post_reply(url, reply_text, data["csrf_token"], data["xf_thread_id"])

        # Закрыть тему
        if replied:
            await self.close_thread(url, data["csrf_token"], data["xf_thread_id"])

        # Уведомление
        errors_preview = "\n".join(validation["errors"][:3]) if not validation["valid"] else ""
        await self.notify(
            f"{status_emoji} Жалоба {status_text}\n"
            f"📌 [{title}]({url})\n"
            f"{errors_preview}"
        )

        config.processed_threads.append(thread_id)
        config.save()
        return "processed"

    async def notify(self, text: str, channel: bool = True):
        """Отправить уведомление в канал и/или боту"""
        try:
            from config import config as cfg
            if channel and cfg.CHANNEL_ID:
                await self.bot.send_message(cfg.CHANNEL_ID, text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка уведомления: {e}")

    async def start_monitoring(self):
        """Основной цикл мониторинга"""
        logger.info("Мониторинг запущен")
        while True:
            if config.is_monitoring and self.is_logged_in:
                try:
                    # Проверяем сессию раз в час
                    session_ok = await self.check_session()
                    if not session_ok:
                        self.is_logged_in = False
                        await self.bot.send_message(
                            config.ADMIN_ID,
                            "⚠️ Сессия форума истекла! Нужна повторная авторизация.\n"
                            "Нажми /login"
                        )
                        await asyncio.sleep(60)
                        continue

                    threads = await self.get_new_threads()
                    logger.info(f"Найдено новых тем: {len(threads)}")

                    for thread in threads:
                        await self.process_thread(thread)
                        await asyncio.sleep(3)  # пауза между обработкой тем

                except Exception as e:
                    logger.error(f"Ошибка мониторинга: {e}")

            await asyncio.sleep(config.check_interval)


async def get_thread_content_safe(monitor: ForumMonitor, url: str):
    try:
        return await monitor.get_thread_content(url)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return None

import json
import os
from dataclasses import dataclass, field
from typing import List

CONFIG_FILE = "runtime_config.json"

@dataclass
class Config:
    # Из .env
    BOT_TOKEN: str = ""
    ADMIN_ID: int = 0
    CHANNEL_ID: str = ""
    
    FORUM_URL: str = "https://forum.majestic-rp.ru/forums/zhaloby-na-igrokov.859/"
    FORUM_LOGIN: str = ""
    FORUM_PASSWORD: str = ""
    
    # Runtime (сохраняются в JSON)
    check_interval: int = 120          # секунды между проверками
    reply_template: str = ""           # шаблон ответа
    is_monitoring: bool = True         # вкл/выкл мониторинг
    processed_threads: List[str] = field(default_factory=list)  # уже обработанные темы
    whitelist_ids: List[int] = field(default_factory=list)       # доп. разрешённые ID
    
    def load(self):
        """Загрузить из .env и runtime JSON"""
        self.BOT_TOKEN = os.getenv("BOT_TOKEN", "")
        self.ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
        self.CHANNEL_ID = os.getenv("CHANNEL_ID", "")
        self.FORUM_LOGIN = os.getenv("FORUM_LOGIN", "")
        self.FORUM_PASSWORD = os.getenv("FORUM_PASSWORD", "")
        
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.check_interval = data.get("check_interval", 120)
                self.reply_template = data.get("reply_template", "")
                self.is_monitoring = data.get("is_monitoring", True)
                self.processed_threads = data.get("processed_threads", [])
                self.whitelist_ids = data.get("whitelist_ids", [])
    
    def save(self):
        """Сохранить runtime настройки"""
        data = {
            "check_interval": self.check_interval,
            "reply_template": self.reply_template,
            "is_monitoring": self.is_monitoring,
            "processed_threads": self.processed_threads[-500:],  # хранить последние 500
            "whitelist_ids": self.whitelist_ids,
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def is_allowed(self, user_id: int) -> bool:
        return user_id == self.ADMIN_ID or user_id in self.whitelist_ids


config = Config()
config.load()

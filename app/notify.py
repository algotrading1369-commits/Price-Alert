import requests

from .db import Database


class TelegramNotifier:
    def __init__(self, db: Database):
        self.db = db

    def config(self):
        return self.db.get_setting("telegram", {"token": "", "chat_id": "", "enabled": False})

    def save_config(self, token, chat_id, enabled):
        self.db.set_setting("telegram", {"token": token, "chat_id": chat_id, "enabled": enabled})

    def send(self, message):
        cfg = self.config()
        if not cfg.get("enabled") or not cfg.get("token") or not cfg.get("chat_id"):
            return False
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{cfg['token']}/sendMessage",
                json={"chat_id": cfg["chat_id"], "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

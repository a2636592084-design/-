"""Telegram Bot 推送。

申请：Telegram 里找 @BotFather 建 bot 拿 token；给 bot 发条消息后，
访问 https://api.telegram.org/bot<token>/getUpdates 里能看到你的 chat_id。
填进 .env 的 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID。
"""
from __future__ import annotations

from .base import Notifier, http_post


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send(self, title: str, message: str) -> None:
        http_post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data={"chat_id": self.chat_id, "text": f"{title}\n{message}"},
        )

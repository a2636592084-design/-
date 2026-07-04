"""控制台通知：写日志。总是启用，是最基础的兜底渠道。"""
from __future__ import annotations

from ..logger import get_logger
from .base import Notifier

log = get_logger("qbot.notify.console")


class ConsoleNotifier(Notifier):
    name = "console"

    def send(self, title: str, message: str) -> None:
        log.info("🔔 %s | %s", title, message)

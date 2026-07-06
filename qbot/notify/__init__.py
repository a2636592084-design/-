"""通知系统：信号变化时主动提醒。

设计：可插拔、按需开启。控制台通知总是开；微信/Telegram/邮件在 .env 填了
对应密钥才启用。任何单个渠道发送失败都不影响其它渠道，更不阻塞交易主流程。
"""
from __future__ import annotations

import os

from ..logger import get_logger
from .base import Notifier
from .console import ConsoleNotifier

log = get_logger("qbot.notify")


def build_from_env() -> list[Notifier]:
    """按环境变量决定启用哪些通知渠道。"""
    notifiers: list[Notifier] = [ConsoleNotifier()]  # 控制台总是启用

    if os.getenv("SERVERCHAN_KEY") or os.getenv("PUSHPLUS_TOKEN"):
        from .serverchan import WeChatNotifier
        notifiers.append(WeChatNotifier(
            serverchan_key=os.getenv("SERVERCHAN_KEY", ""),
            pushplus_token=os.getenv("PUSHPLUS_TOKEN", ""),
        ))
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        from .telegram import TelegramNotifier
        notifiers.append(TelegramNotifier(
            token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        ))
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_TO"):
        from .email_smtp import EmailNotifier
        notifiers.append(EmailNotifier(
            host=os.getenv("SMTP_HOST", ""),
            port=int(os.getenv("SMTP_PORT", "465")),
            user=os.getenv("SMTP_USER", ""),
            password=os.getenv("SMTP_PASS", ""),
            to=os.getenv("SMTP_TO", ""),
        ))

    names = ", ".join(n.name for n in notifiers)
    log.info("已启用通知渠道: %s", names)
    return notifiers


def notify_all(title: str, message: str, notifiers: list[Notifier] | None = None) -> None:
    """向所有启用的渠道发送。best-effort：单个失败只记日志，不抛出。"""
    if notifiers is None:
        notifiers = build_from_env()
    for n in notifiers:
        try:
            n.send(title, message)
        except Exception as e:  # noqa: BLE001
            log.warning("通知渠道 %s 发送失败: %s", n.name, e)


__all__ = ["Notifier", "build_from_env", "notify_all"]

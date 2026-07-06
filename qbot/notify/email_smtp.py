"""邮件通知（标准库 smtplib，无额外依赖）。

以 QQ 邮箱为例：设置 → 账户 → 开启 SMTP 服务 → 拿"授权码"（不是登录密码）。
.env 填：SMTP_HOST=smtp.qq.com  SMTP_PORT=465  SMTP_USER=你的邮箱
        SMTP_PASS=授权码  SMTP_TO=收件邮箱
"""
from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from .base import Notifier


class EmailNotifier(Notifier):
    name = "email"

    def __init__(self, host: str, port: int, user: str, password: str, to: str):
        self.host, self.port = host, port
        self.user, self.password, self.to = user, password, to

    def send(self, title: str, message: str) -> None:
        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = title
        msg["From"] = self.user
        msg["To"] = self.to
        # 465 用 SSL，587/25 用普通连接 + STARTTLS
        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=15) as s:
                s.login(self.user, self.password)
                s.sendmail(self.user, [self.to], msg.as_string())
        else:
            with smtplib.SMTP(self.host, self.port, timeout=15) as s:
                s.starttls()
                s.login(self.user, self.password)
                s.sendmail(self.user, [self.to], msg.as_string())

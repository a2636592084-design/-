"""微信推送：Server酱(sctapi.ftqq.com) 与 PushPlus(pushplus.plus)。

国内最实用的手机推送，人不在电脑前也能收到。
申请（任选其一或都填）：
  · Server酱：https://sct.ftqq.com  微信扫码登录 → 拿 SendKey
  · PushPlus：https://www.pushplus.plus  微信扫码登录 → 拿 token
把拿到的值填进 .env 的 SERVERCHAN_KEY / PUSHPLUS_TOKEN。
"""
from __future__ import annotations

from .base import Notifier, http_post


class WeChatNotifier(Notifier):
    name = "wechat"

    def __init__(self, serverchan_key: str = "", pushplus_token: str = ""):
        self.serverchan_key = serverchan_key
        self.pushplus_token = pushplus_token

    def send(self, title: str, message: str) -> None:
        if self.serverchan_key:
            http_post(
                f"https://sctapi.ftqq.com/{self.serverchan_key}.send",
                data={"title": title, "desp": message},
            )
        if self.pushplus_token:
            http_post(
                "https://www.pushplus.plus/send",
                json_body={"token": self.pushplus_token, "title": title,
                           "content": message, "template": "txt"},
            )

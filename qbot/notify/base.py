"""通知渠道基类 + 共享的 HTTP 工具（代理/CA 兼容）。"""
from __future__ import annotations

import os


class Notifier:
    name: str = "base"

    def send(self, title: str, message: str) -> None:
        raise NotImplementedError


def http_post(url: str, *, data: dict | None = None, json_body: dict | None = None,
              timeout: int = 15) -> str:
    """带代理/CA 兼容的 POST。受管沙箱有 HTTPS_PROXY 时自动走代理，本地则直连。"""
    import requests

    ca = os.environ.get("SSL_CERT_FILE")
    if not ca or not os.path.exists(ca):
        ca = "/root/.ccr/ca-bundle.crt"
    verify = ca if os.path.exists(ca) else True
    r = requests.post(url, data=data, json=json_body, timeout=timeout, verify=verify)
    r.raise_for_status()
    return r.text

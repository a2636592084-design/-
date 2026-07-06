#!/usr/bin/env python3
"""启动可视化面板。

    python run_dashboard.py
然后浏览器打开 http://127.0.0.1:8000

监听地址可用环境变量覆盖（云服务器部署用）：
    QBOT_HOST=0.0.0.0  QBOT_PORT=8000
⚠️ 安全提醒：面板没有登录验证。放到公网服务器时，请【不要】直接绑 0.0.0.0
   暴露到公网，而应保持默认 127.0.0.1，通过 SSH 隧道访问（见 docs/部署指南.md）。
"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    host = os.getenv("QBOT_HOST", "127.0.0.1")
    port = int(os.getenv("QBOT_PORT", "8000"))
    uvicorn.run("qbot.web.app:app", host=host, port=port, reload=False)

"""面板接口冒烟测试（用合成数据，无需联网）。

httpx 未安装时自动跳过（TestClient 依赖它），不影响核心测试。
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from qbot.web.app import app  # noqa: E402

client = TestClient(app)


def test_index_and_strategies():
    assert client.get("/").status_code == 200
    strategies = client.get("/api/strategies").json()
    assert "regime_switch" in strategies


def test_backtest_endpoint_synthetic():
    r = client.get("/api/backtest", params={"market": "synthetic",
                                            "strategy": "donchian", "limit": 400})
    d = r.json()
    assert r.status_code == 200
    assert len(d["equity"]) == 400
    assert "cagr" in d["metrics"]


def test_scan_endpoint_synthetic():
    r = client.get("/api/scan", params={"market": "synthetic",
                                        "symbols": "A,B,C", "strategy": "ma_cross"})
    rows = r.json()
    assert r.status_code == 200
    assert len(rows) == 3
    for row in rows:
        assert "target" in row and "adx" in row and "rsi" in row

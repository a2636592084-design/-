"""共振面板测试：19 指标、投票范围、计数一致、趋势方向、字段完整。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbot.data.synthetic import synthetic_ohlcv
from qbot.strategies.resonance import INDICATORS, resonance_votes


def _trend(start, end, n=200):
    close = np.linspace(start, end, n) + np.sin(np.linspace(0, 6 * np.pi, n)) * 3
    return pd.DataFrame({
        "open": close - 0.3, "high": close + 1.5, "low": close - 1.5,
        "close": close, "volume": np.abs(np.sin(np.arange(n))) * 1e6 + 1e5,
    }, index=pd.date_range("2024-01-01", periods=n, freq="D"))


def test_has_19_indicators():
    assert len(INDICATORS) == 19
    keys = [k for k, _, _ in INDICATORS]
    assert len(set(keys)) == 19          # 无重复 key


def test_votes_range_and_counts():
    r = resonance_votes(synthetic_ohlcv(periods=400, seed=3))
    assert len(r["indicators"]) == 19
    for it in r["indicators"]:
        assert it["vote"] in (-1, 0, 1)
        assert set(it) >= {"key", "name", "group", "vote"}
    assert r["bull"] + r["bear"] + r["neutral"] == r["total"] == 19


def test_uptrend_mostly_bull():
    r = resonance_votes(_trend(100, 180))
    assert r["bull"] > r["bear"]
    assert r["bull"] >= 10


def test_downtrend_mostly_bear():
    r = resonance_votes(_trend(180, 100))
    assert r["bear"] > r["bull"]
    assert r["bear"] >= 10


def test_too_few_bars():
    assert "error" in resonance_votes(synthetic_ohlcv(periods=400, seed=1).iloc[:12])

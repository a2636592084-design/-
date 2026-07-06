"""高阶结构分析测试：道氏/SMC/缠论 的范围、因果性、字段完整、趋势识别。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbot.data.synthetic import synthetic_ohlcv
from qbot.strategies.structure import (
    _fractals,
    _pivots,
    chan_theory,
    dow_theory,
    structure_overlays,
    structure_report,
)


def _ramp(n=200, start=100.0, end=200.0, amp=14.0, freq=6.0):
    """带回调的单向行情：整体从 start 到 end，叠加正弦波段（制造显著摆动高低点）。

    波段振幅（2×amp）需明显大于日内波幅(≈4)×阈值倍数，_pivots 才会认这些波段。
    """
    base = np.linspace(start, end, n)
    close = base + np.sin(np.linspace(0, freq * np.pi, n)) * amp
    return pd.DataFrame({
        "open": close - 0.5, "high": close + 2, "low": close - 2,
        "close": close, "volume": np.ones(n) * 1000.0,
    }, index=pd.date_range("2024-01-01", periods=n, freq="D"))


def test_report_fields_complete():
    df = synthetic_ohlcv(periods=400, seed=3)
    r = structure_report(df)
    for key in ("gauge", "score", "verdict", "dow", "smc", "chan", "swings", "text"):
        assert key in r
    assert -1.0 <= r["score"] <= 1.0
    assert 0 <= r["gauge"] <= 100
    assert set(r["dow"]) >= {"trend", "score", "desc"}
    assert set(r["chan"]) >= {"fractals", "bi", "center", "score"}


def test_uptrend_detected():
    """明确的抬高结构 → 道氏上升、结构偏多。"""
    r = structure_report(_ramp(start=100, end=180))
    assert r["dow"]["trend"] == "up"
    assert r["score"] > 0
    # 摆动高、低点都应逐个抬高
    assert r["dow"]["highs"][1] > r["dow"]["highs"][0]
    assert r["dow"]["lows"][1] > r["dow"]["lows"][0]


def test_downtrend_detected():
    r = structure_report(_ramp(start=180, end=100))
    assert r["dow"]["trend"] == "down"
    assert r["score"] < 0


def test_pivots_alternate():
    """阈值 ZigZag 的结构点必须严格高低交替。"""
    df = synthetic_ohlcv(periods=500, seed=9)
    zz = _pivots(df)
    kinds = [p[2] for p in zz]
    for a, b in zip(kinds, kinds[1:]):
        assert a != b


def test_pivots_filters_noise():
    """阈值 ZigZag 应比原始 3根K分型少得多（过滤了噪声波段）。"""
    df = synthetic_ohlcv(periods=500, seed=4)
    assert len(_pivots(df)) < len(_fractals(df, k=1))


def test_causal_no_lookahead():
    """截断到前缀，已确认的结构点（去掉最右若干待确认点）应保持一致。"""
    df = synthetic_ohlcv(periods=400, seed=7)
    full = _pivots(df)
    cut = _pivots(df.iloc[:250])
    # 落在前缀早段的已确认摆动点，两次结果必须一致（最右侧待确认点除外）
    safe = [p for p in full if p[0] < 220]
    cut_set = {(p[0], round(p[1], 4), p[2]) for p in cut}
    for p in safe:
        assert (p[0], round(p[1], 4), p[2]) in cut_set


def test_chan_center_zone_valid():
    """若形成中枢，其上沿必须严格高于下沿。"""
    df = synthetic_ohlcv(periods=800, seed=2)
    c = chan_theory(df)["center"]
    if c is not None:
        assert c["zg"] > c["zd"]


def test_dow_insufficient_points():
    """结构点不足时优雅返回 range，不报错。"""
    d = dow_theory([(0, 100.0, "H")])
    assert d["trend"] == "range"


def test_too_few_bars():
    df = synthetic_ohlcv(periods=400, seed=1).iloc[:10]
    r = structure_report(df)
    assert "error" in r


def test_overlays_geometry():
    """画图数据结构完整、时间戳与价位合理（供前端直接叠加到K线）。"""
    df = synthetic_ohlcv(periods=500, seed=6)
    o = structure_overlays(df)
    assert set(o) >= {"dow", "chan", "smc"}
    # 道氏摆动点带标注
    for s in o["dow"]["swings"]:
        assert set(s) >= {"t", "price", "kind", "label"}
        assert s["kind"] in ("H", "L")
    # 缠论笔是折线点，中枢上沿高于下沿
    assert all(set(p) >= {"t", "price"} for p in o["chan"]["bi"])
    for c in o["chan"]["centers"]:
        assert c["zg"] > c["zd"] and c["t2"] >= c["t1"]
    # SMC 事件类型合法
    for e in o["smc"]["events"]:
        assert e["type"] in ("BOS", "CHoCH") and e["dir"] in ("up", "down")
    # 缺口/订单块 top>bottom
    for f in o["smc"]["fvgs"]:
        assert f["top"] > f["bottom"]
    for b in o["smc"]["order_blocks"]:
        assert b["high"] > b["low"]


def test_overlays_too_few_bars():
    df = synthetic_ohlcv(periods=400, seed=1).iloc[:12]
    assert "error" in structure_overlays(df)

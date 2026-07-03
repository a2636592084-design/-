"""指标正确性 + 无未来函数测试。

守护 10 个技术指标的数学正确性，以及组合策略不引入未来数据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbot.data.synthetic import synthetic_ohlcv
from qbot.strategies import REGISTRY
from qbot.strategies.indicators import (
    adx, atr, bollinger, ema, macd, obv, rsi, sma,
    stoch_rsi, supertrend, volume_profile_poc, vwap, vwma,
)


def _df():
    return synthetic_ohlcv(periods=400, seed=3)


def test_sma_matches_manual():
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert sma(s, 3).iloc[-1] == 4.0   # (3+4+5)/3


def test_rsi_bounds():
    r = rsi(_df()["close"], 14).dropna()
    assert (r >= 0).all() and (r <= 100).all()


def test_stoch_rsi_bounds():
    k, d = stoch_rsi(_df()["close"])
    k = k.dropna()
    assert (k >= -1e-6).all() and (k <= 100 + 1e-6).all()


def test_bollinger_order():
    """上轨 >= 中轨 >= 下轨，恒成立。"""
    mid, up, lo, pb, bw = bollinger(_df()["close"])
    m = pd.concat([mid, up, lo], axis=1).dropna()
    assert (m.iloc[:, 1] >= m.iloc[:, 0]).all()   # up >= mid
    assert (m.iloc[:, 0] >= m.iloc[:, 2]).all()   # mid >= lo


def test_adx_nonnegative():
    a, pdi, mdi = adx(_df())
    a = a.dropna()
    assert (a >= 0).all() and (a <= 100).all()


def test_atr_positive():
    assert (atr(_df()).dropna() > 0).all()


def test_macd_hist_identity():
    line, sig, hist = macd(_df()["close"])
    diff = (hist - (line - sig)).dropna().abs()
    assert (diff < 1e-9).all()


def test_obv_direction():
    """价格连涨时 OBV 应单调不减。"""
    close = pd.Series([1.0, 2, 3, 4, 5])
    vol = pd.Series([10.0, 10, 10, 10, 10])
    o = obv(close, vol)
    assert (o.diff().dropna() >= 0).all()


def test_supertrend_direction_values():
    st, d = supertrend(_df())
    assert set(np.unique(d)).issubset({-1.0, 1.0})


def test_vwap_vwma_finite():
    df = _df()
    assert np.isfinite(vwap(df).dropna()).all()
    assert np.isfinite(vwma(df["close"], df["volume"], 20).dropna()).all()


def test_volume_profile_within_range():
    df = _df()
    poc = volume_profile_poc(df, window=50, bins=20).dropna()
    # POC 必须落在近端价格区间内（用宽松边界）
    assert (poc > df["low"].min()).all() and (poc < df["high"].max()).all()


def test_all_strategies_run():
    """所有注册策略都能在合成数据上跑出与输入等长的仓位序列。"""
    df = _df()
    for name, cls in REGISTRY.items():
        pos = cls().generate_positions(df)
        assert len(pos) == len(df), f"{name} 仓位长度不符"
        assert pos.fillna(0).between(-1, 1).all(), f"{name} 仓位越界"


def test_composite_no_lookahead():
    """趋势栈策略在'未来暴涨'前不应提前满仓（信号只能用当前及过去）。"""
    idx = pd.date_range("2021-01-01", periods=60, freq="1D")
    # 前 50 根横盘，后 10 根暴涨；横盘期趋势栈不该给出多头信号
    price = np.concatenate([np.full(50, 100.0), np.linspace(100, 300, 10)])
    df = pd.DataFrame({"open": price, "high": price * 1.001,
                       "low": price * 0.999, "close": price,
                       "volume": 1000.0}, index=idx)
    pos = REGISTRY["trend_stack"]().generate_positions(df)
    assert pos.iloc[:45].sum() == 0.0   # 横盘期无信号，未偷看未来

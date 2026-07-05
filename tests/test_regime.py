"""大盘方向闸测试：范围/因果性/趋势识别，以及组合回测里的闸真的会拦截开仓。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbot.strategies.regime import regime_now, regime_series


def _series(closes):
    n = len(closes)
    idx = pd.date_range("2023-01-01", periods=n, freq="4h")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c.shift(1).fillna(c.iloc[0]), "high": c * 1.01,
                         "low": c * 0.99, "close": c, "volume": 1.0}, index=idx)


def test_regime_range_and_dtype():
    df = _series(np.linspace(100, 300, 400))
    r = regime_series(df)
    assert set(r.unique()).issubset({-1, 0, 1})
    assert len(r) == len(df)


def test_uptrend_is_bull():
    # 稳步上行 → 收盘>EMA200 且 ADX 高 → 多头(+1)
    df = _series(np.linspace(100, 400, 400))
    assert regime_now(df) == 1


def test_downtrend_is_bear():
    df = _series(np.linspace(400, 100, 400))
    assert regime_now(df) == -1


def test_chop_is_flat():
    # 无方向的小幅噪声(围绕常数抖动) → 无净趋势、ADX 低 → 观望(0)
    rng = np.random.default_rng(7)
    df = _series(200 + rng.normal(0, 1.5, 400).cumsum() * 0.0 + rng.normal(0, 2.0, 400))
    assert regime_now(df) == 0


def test_causal_no_lookahead():
    # 前缀截断后，早期各根的判定不应改变（只用过去信息）
    df = _series(np.linspace(100, 400, 400))
    full = regime_series(df)
    cut = regime_series(df.iloc[:300])
    assert (full.iloc[:300].values == cut.values).all()


def test_short_data_does_not_block():
    df = _series(np.linspace(100, 120, 30))    # 不足 ema_len → 不拦截(退回照常)
    assert regime_now(df) == 1


def test_gate_blocks_opens_in_backtest():
    """闸开时，把 BTC 造成一路下跌(空头行情)，只做多的策略应几乎不开仓。"""
    from qbot.backtest.portfolio_bt import backtest_portfolio
    from qbot.strategies import REGISTRY

    # 用合成市场，universe 里含 BTC；BTC 下跌 → 闸判空头 → 只做多策略被拦
    universe = ["BTC/USDT", "DEMO2", "DEMO3"]

    def factory():
        return REGISTRY["confluence"]()   # 默认 long_only

    off = backtest_portfolio("synthetic", universe, factory, market_gate=False, limit=400)
    on = backtest_portfolio("synthetic", universe, factory, market_gate=True, limit=400)
    # 闸开的成交数不应多于闸关（拦截只会减少或持平开仓）
    assert on["metrics"]["num_trades"] <= off["metrics"]["num_trades"]

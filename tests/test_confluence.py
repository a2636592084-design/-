"""多指标共振策略测试：打分范围、ADX 闸、因果性、透明化、端到端回测。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbot.backtest import Backtester
from qbot.data.synthetic import synthetic_ohlcv
from qbot.strategies import REGISTRY
from qbot.strategies.confluence import ConfluenceStrategy


def test_score_in_range():
    """共振分必须恒在 [-1, 1]。"""
    df = synthetic_ohlcv(periods=600, seed=3)
    v = ConfluenceStrategy()._votes(df)
    s = v["score"].dropna()
    assert (s >= -1.0 - 1e-9).all() and (s <= 1.0 + 1e-9).all()


def test_adx_gate_forces_flat():
    """ADX 低于闸值时一律空仓：把 adx_min 设很高，应几乎不开仓。"""
    df = synthetic_ohlcv(periods=800, seed=5)
    pos = ConfluenceStrategy(adx_min=99).generate_positions(df)
    assert pos.sum() == 0.0   # ADX 永远达不到 99，永远空仓


def test_position_only_long_and_binary():
    df = synthetic_ohlcv(periods=500, seed=1)
    pos = ConfluenceStrategy().generate_positions(df)
    assert set(np.unique(pos)).issubset({0.0, 1.0})


def test_causal_no_lookahead():
    """截断数据后，公共前缀的仓位必须完全一致（只用当前及过去）。"""
    df = synthetic_ohlcv(periods=400, seed=7)
    strat = ConfluenceStrategy()
    full = strat.generate_positions(df)
    cut = strat.generate_positions(df.iloc[:250])
    pd.testing.assert_series_equal(full.iloc[:250], cut, check_names=False)


def test_explain_fields():
    df = synthetic_ohlcv(periods=400, seed=2)
    info = ConfluenceStrategy().explain(df)
    assert "score" in info and "votes" in info and "conclusion" in info
    assert set(info["votes"]) == {"ema", "supertrend", "macd", "rsi", "bbands", "volume"}
    assert -1.0 <= info["score"] <= 1.0


def test_registered_and_backtests():
    assert "confluence" in REGISTRY
    df = synthetic_ohlcv(periods=800, seed=9)
    res = Backtester().run(df, REGISTRY["confluence"]())
    assert len(res.equity) == len(df)
    for k in ("cagr", "max_drawdown", "sharpe", "num_trades"):
        assert k in res.metrics


def test_lower_enter_thr_trades_more():
    """开仓阈值越低，成交次数应不减少（更容易触发）。"""
    df = synthetic_ohlcv(periods=1000, seed=11)
    loose = Backtester().run(df, ConfluenceStrategy(enter_thr=0.3)).metrics["num_trades"]
    tight = Backtester().run(df, ConfluenceStrategy(enter_thr=0.8)).metrics["num_trades"]
    assert loose >= tight

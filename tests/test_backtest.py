"""核心正确性测试：防未来函数、指标、风控熔断。

这些测试守护系统的"诚实性"——一旦有人不小心引入未来函数或改坏风控，
测试会立刻变红。跑：  pytest tests/  或  python -m pytest tests/
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbot.backtest import Backtester
from qbot.backtest.metrics import compute_metrics
from qbot.data.synthetic import synthetic_ohlcv
from qbot.risk.manager import RiskConfig, RiskManager
from qbot.strategies import MACrossStrategy, RSIReversionStrategy


def test_synthetic_reproducible():
    """同一 seed 必须产生完全相同的数据（可复现是量化的底线）。"""
    a = synthetic_ohlcv(periods=200, seed=7)
    b = synthetic_ohlcv(periods=200, seed=7)
    pd.testing.assert_frame_equal(a, b)


def test_no_lookahead():
    """核心：t 时刻的信号只能在 t+1 生效。

    构造一个"作弊"策略：它想在价格突然暴涨的那一根就满仓吃到。
    因为引擎用 shift(1)，它无法吃到当根的涨幅——收益必须来自之后。
    """
    idx = pd.date_range("2021-01-01", periods=10, freq="1D")
    price = pd.Series([100, 100, 100, 100, 200, 200, 200, 200, 200, 200], index=idx,
                      dtype=float)
    df = pd.DataFrame({"open": price, "high": price, "low": price,
                       "close": price, "volume": 1.0}, index=idx)

    class CheatStrategy(MACrossStrategy):
        # 只在暴涨那一根（index 4）之前给出满仓信号，测试能否吃到跳涨
        def generate_positions(self, d):
            pos = pd.Series(0.0, index=d.index)
            pos.iloc[3] = 1.0   # 在 t=3（涨之前）就想满仓
            return pos

    bt = Backtester(initial_capital=1000, fee_rate=0, slippage=0)
    res = bt.run(df, CheatStrategy())
    # t=3 的信号在 t=4 生效，正好吃到 100->200 的跳涨，净值应约翻倍
    assert res.equity.iloc[-1] > 1900
    # 若错误地"当根生效"，会吃不到或算错——这里确保逻辑正确
    assert res.positions.iloc[3] == 0.0   # t=3 尚未持仓
    assert res.positions.iloc[4] == 1.0   # t=4 才持仓


def test_metrics_positive_drawdown_sign():
    """最大回撤必须为负或零，年化在单调上涨曲线上必须为正。"""
    eq = pd.Series(np.linspace(100, 200, 253))  # 一年翻倍
    m = compute_metrics(eq, periods_per_year=252)
    assert m["max_drawdown"] <= 0
    assert m["cagr"] > 0.9   # 约 100% 年化


def test_risk_position_cap():
    """风控必须把超限仓位收敛到上限。"""
    rm = RiskManager(RiskConfig(max_position_per_symbol=0.2))
    d = rm.evaluate(target_position=1.0, equity=100000)
    assert d.allow is True
    assert abs(d.adjusted_position - 0.2) < 1e-9


def test_risk_drawdown_halt():
    """组合回撤超阈值必须触发熔断，之后一律拒绝开仓。"""
    rm = RiskManager(RiskConfig(max_portfolio_drawdown=0.2))
    rm.update_equity(100000)          # 峰值
    d = rm.evaluate(0.5, equity=75000)  # 回撤 25% > 20%
    assert rm.halted is True
    assert d.allow is False
    assert d.adjusted_position == 0.0


def test_position_sizing_by_risk():
    """按 1% 风险定仓位：亏损单位额确定时，数量应符合公式。"""
    rm = RiskManager(RiskConfig(risk_per_trade=0.01, max_position_per_symbol=1.0))
    units = rm.position_size_by_risk(price=100, stop_price=90, equity=100000)
    # 每笔风险=1000元，单位风险=10元 => 100 单位
    assert abs(units - 100) < 1e-6


def test_full_backtest_runs():
    """端到端冒烟测试：真实策略在合成数据上能跑出完整指标。"""
    df = synthetic_ohlcv(periods=500, seed=1)
    res = Backtester().run(df, RSIReversionStrategy())
    for key in ("cagr", "max_drawdown", "sharpe", "win_rate", "num_trades"):
        assert key in res.metrics
    assert len(res.equity) == len(df)

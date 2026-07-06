"""进阶功能测试：ATR 移动止损、ADX 自适应组合、样本外优化。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbot.backtest import Backtester
from qbot.backtest.optimize import PARAM_GRIDS, walk_forward
from qbot.data.synthetic import synthetic_ohlcv
from qbot.risk.stops import apply_atr_trailing_stop
from qbot.strategies import REGISTRY, RegimeSwitchStrategy


def test_atr_stop_forces_exit_on_crash():
    """持多单后价格断崖式下跌，ATR 止损必须强制离场（不会一直扛单）。"""
    idx = pd.date_range("2021-01-01", periods=40, freq="1D")
    # 前 20 根稳涨（进场并推高 peak），后 20 根暴跌
    up = np.linspace(100, 140, 20)
    down = np.linspace(139, 60, 20)
    price = np.concatenate([up, down])
    df = pd.DataFrame({"open": price, "high": price * 1.005,
                       "low": price * 0.995, "close": price,
                       "volume": 1000.0}, index=idx)
    always_long = pd.Series(1.0, index=idx)   # 策略始终想满仓
    stopped = apply_atr_trailing_stop(df, always_long, mult=3.0, atr_n=14)
    # 暴跌段结束前必须已被止损离场
    assert stopped.iloc[-1] == 0.0
    assert (stopped.iloc[25:] == 0.0).any()


def test_atr_stop_is_causal():
    """止损输出只依赖当前及过去：截断数据后，公共前缀结果必须一致。"""
    df = synthetic_ohlcv(periods=300, seed=5)
    pos = REGISTRY["trend_stack"]().generate_positions(df).clip(lower=0)
    full = apply_atr_trailing_stop(df, pos, mult=3.0)
    cut = apply_atr_trailing_stop(df.iloc[:200], pos.iloc[:200], mult=3.0)
    pd.testing.assert_series_equal(full.iloc[:200], cut, check_names=False)


def test_atr_stop_reduces_drawdown():
    """在趋势策略上叠加 ATR 止损，通常应降低最大回撤（此合成样本上验证）。"""
    df = synthetic_ohlcv(periods=1500, seed=42)
    strat = REGISTRY["supertrend"]()
    dd_off = Backtester().run(df, strat).metrics["max_drawdown"]
    dd_on = Backtester(atr_stop_mult=2.0).run(df, strat).metrics["max_drawdown"]
    # 止损后回撤不应更深（允许相等的边界情况）
    assert dd_on >= dd_off - 1e-9


def test_regime_switch_mutually_exclusive():
    """趋势子策略与震荡子策略绝不同时开仓（ADX 区间互斥）。"""
    df = synthetic_ohlcv(periods=800, seed=9)
    rs = RegimeSwitchStrategy()
    trend = rs.trend.generate_positions(df)
    rng = rs.range.generate_positions(df)
    both = (trend > 0) & (rng > 0)
    assert not both.any()


def test_walk_forward_runs_and_reports_oos():
    """样本外优化能跑，且每折都给出 IS/OOS 分数。"""
    df = synthetic_ohlcv(periods=1200, seed=11)
    folds = walk_forward(df, REGISTRY["ma_cross"], PARAM_GRIDS["ma_cross"],
                         n_splits=3, objective="calmar")
    assert len(folds) >= 1
    for f in folds:
        assert isinstance(f.best_params, dict) and f.best_params
        assert isinstance(f.oos_score, float)

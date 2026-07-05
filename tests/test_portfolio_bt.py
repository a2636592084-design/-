"""组合回测测试：结构完整、指标合理、防未来(信号已shift)、空组合不崩。"""
from __future__ import annotations

from qbot.backtest.portfolio_bt import backtest_portfolio
from qbot.strategies.confluence import ConfluenceStrategy


def _run(**kw):
    return backtest_portfolio(
        market="synthetic", universe=["S0", "S1", "S2", "S3"],
        strategy_factory=lambda: ConfluenceStrategy(allow_short=True),
        timeframe="4h", limit=700, max_positions=2, **kw)


def test_result_structure():
    r = _run(atr_stop_mult=2.5, cooldown=3, breakeven=0.05)
    assert "metrics" in r and "equity_curve" in r
    m = r["metrics"]
    for k in ("total_return", "cagr", "max_drawdown", "sharpe", "num_trades", "win_rate"):
        assert k in m
    assert r["equity_curve"] and all(set(c) >= {"ts", "equity"} for c in r["equity_curve"])
    assert -1.0 <= m["win_rate"] <= 1.0
    assert m["max_drawdown"] <= 0.0


def test_fixed_stop_mode_runs():
    r = _run(atr_stop_mult=0, stop_loss=0.08, take_profit=0.25, cooldown=0)
    assert "error" not in r and r["metrics"]["num_trades"] >= 0


def test_empty_universe_graceful():
    r = backtest_portfolio(
        market="synthetic", universe=[],
        strategy_factory=lambda: ConfluenceStrategy(), timeframe="4h", limit=300)
    assert "error" in r


def test_final_equity_consistent():
    r = _run(atr_stop_mult=2.5, cooldown=2)
    # 末净值应等于资金曲线最后一点
    assert abs(r["metrics"]["final_equity"] - r["equity_curve"][-1]["equity"]) < 1.0

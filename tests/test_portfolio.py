"""全市场组合引擎测试：多标的持仓、上限、转空平仓、熔断清仓。"""
from __future__ import annotations

import pandas as pd

from qbot.broker.paper import PaperBroker
from qbot.engine.portfolio import PortfolioEngine
from qbot.risk.manager import RiskConfig, RiskManager
from qbot.strategies.base import Strategy


class AllLong(Strategy):
    name = "all_long"
    def generate_positions(self, df):
        return pd.Series(1.0, index=df.index)


class AllFlat(Strategy):
    name = "all_flat"
    def generate_positions(self, df):
        return pd.Series(0.0, index=df.index)


def _engine(strategy, max_positions=5, risk=None, execute=True):
    universe = [f"S{i}" for i in range(20)]
    return PortfolioEngine(
        mode="test", market="synthetic", universe=universe, strategy=strategy,
        broker=PaperBroker(cash=100_000), risk=risk or RiskManager(RiskConfig()),
        max_positions=max_positions, lookback=200, execute=execute,
    )


def test_holds_at_most_max_positions():
    eng = _engine(AllLong(), max_positions=5)
    st = eng.tick()
    assert len(st["positions"]) == 5          # 20个都做多，但只持有上限 5 个
    assert st["scanned"] == 20


def test_signal_turned_flat_sells_all():
    eng = _engine(AllLong(), max_positions=5)
    eng.tick()
    assert len(eng.broker.get_account().positions) >= 1
    eng.strategy = AllFlat()                   # 信号全转空
    st = eng.tick()
    assert len(st["positions"]) == 0           # 应全部平仓


def test_drawdown_halt_closes_all_and_blocks():
    risk = RiskManager(RiskConfig(max_portfolio_drawdown=0.2))
    eng = _engine(AllLong(), max_positions=5, risk=risk)
    eng.tick()
    # 人为制造回撤触发熔断
    risk.update_equity(1_000_000)              # 抬高峰值
    risk.update_equity(500_000)                # 回撤 50% → 熔断
    st = eng.tick()
    assert risk.halted is True
    assert len(st["positions"]) == 0           # 熔断即清仓


def test_signals_only_does_not_trade():
    eng = _engine(AllLong(), max_positions=5, execute=False)
    st = eng.tick()
    assert st["execute"] is False
    assert len(st["positions"]) == 0           # 只出信号，不下单
    assert len(st["scan"]) > 0                 # 但扫描结果照常给出


def test_state_written_to_disk():
    eng = _engine(AllLong())
    st = eng.tick()
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "logs" / "portfolio_test.json"
    assert p.exists()
    assert st["mode"] == "test" and "positions" in st and "scan" in st

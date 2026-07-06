"""双向（做多/做空）交易测试：策略产空信号、引擎开空/反转平仓/做空盈亏。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from qbot.broker.paper import PaperBroker
from qbot.broker.base import Order
from qbot.engine.portfolio import PortfolioEngine
from qbot.risk.manager import RiskConfig, RiskManager
from qbot.strategies.base import Strategy
from qbot.strategies.confluence import ConfluenceStrategy


def _downtrend(n=300):
    idx = pd.date_range("2021-01-01", periods=n, freq="1D")
    price = pd.Series(np.linspace(200, 60, n), index=idx)
    return pd.DataFrame({"open": price, "high": price * 1.01, "low": price * 0.99,
                         "close": price, "volume": 1000.0}, index=idx)


def test_confluence_long_only_never_shorts():
    pos = ConfluenceStrategy(allow_short=False).generate_positions(_downtrend())
    assert ConfluenceStrategy(allow_short=False).long_only is True
    assert (pos >= 0).all()          # 只做多，绝不出现 -1


def test_confluence_allow_short_shorts_downtrend():
    s = ConfluenceStrategy(allow_short=True)
    assert s.long_only is False
    pos = s.generate_positions(_downtrend())
    assert (pos == -1).any()         # 下跌趋势里会做空


def test_confluence_two_way_causal():
    df = _downtrend(260)
    s = ConfluenceStrategy(allow_short=True)
    full = s.generate_positions(df)
    cut = s.generate_positions(df.iloc[:180])
    pd.testing.assert_series_equal(full.iloc[:180], cut, check_names=False)


class _Fixed(Strategy):
    name = "fixed"
    long_only = False
    def __init__(self, v): self.v = v
    def generate_positions(self, df): return pd.Series(self.v, index=df.index)


def _engine(strat, mp=3):
    return PortfolioEngine(mode="t", market="synthetic",
                           universe=["A", "B", "C"], strategy=strat,
                           broker=PaperBroker(cash=100_000),
                           risk=RiskManager(RiskConfig()), max_positions=mp, lookback=200)


def test_engine_opens_shorts():
    st = _engine(_Fixed(-1.0)).tick()
    assert len(st["positions"]) == 3
    assert all(p["side"] == "short" for p in st["positions"])


def test_engine_reverses_short_to_long():
    eng = _engine(_Fixed(-1.0)); eng.tick()
    eng.strategy = _Fixed(1.0)
    st = eng.tick()
    assert all(p["side"] == "long" for p in st["positions"])   # 反转后全变多


def test_engine_flattens_on_neutral():
    eng = _engine(_Fixed(1.0)); eng.tick()
    eng.strategy = _Fixed(0.0)
    st = eng.tick()
    assert len(st["positions"]) == 0                           # 观望即全平


def test_paper_short_pnl_direction():
    """做空后价格下跌应盈利（浮盈为正）。"""
    b = PaperBroker(cash=100_000, fee_rate=0.0, slippage=0.0)
    b.set_price("X", 100.0)
    b.submit(Order("X", "sell", 10))      # 开空 10 @ 100
    pos = b.get_account().positions["X"]
    assert pos.amount == -10 and pos.avg_price == 100.0
    # 价格跌到 90：做空盈利
    b.set_price("X", 90.0)
    eq = b.get_account().equity({"X": 90.0})
    assert eq > 100_000                    # 空单赚了钱

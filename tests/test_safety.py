"""保命闭环测试：交易日志(已实现盈亏/重启续接) + 单日亏损熔断 + 引擎联动。"""
from __future__ import annotations

import pandas as pd
import pytest

import qbot.journal as J
from qbot.broker.base import Order
from qbot.broker.paper import PaperBroker
from qbot.engine.portfolio import PortfolioEngine
from qbot.journal import TradeJournal
from qbot.risk.manager import RiskConfig, RiskManager
from qbot.strategies.base import Strategy


class AllLong(Strategy):
    name = "all_long"
    def generate_positions(self, df):
        return pd.Series(1.0, index=df.index)


@pytest.fixture()
def tmpdir_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_DIR", tmp_path)
    return tmp_path


# ------------------------------------------------ 单日亏损熔断
def test_daily_loss_breaker_and_reset():
    risk = RiskManager(RiskConfig(max_daily_loss=0.10))
    risk.update_equity(100.0, today="2026-01-01")      # 当日起点 100
    assert not risk.daily_halted
    risk.update_equity(93.0, today="2026-01-01")       # -7% 未到
    assert not risk.daily_halted
    risk.update_equity(88.0, today="2026-01-01")       # -12% → 熔断
    assert risk.daily_halted
    assert risk.daily_loss_pct(88.0) == -12.0
    risk.update_equity(95.0, today="2026-01-02")       # 次日复位
    assert not risk.daily_halted


def test_daily_and_portfolio_halt_independent():
    risk = RiskManager(RiskConfig(max_daily_loss=0.10, max_portfolio_drawdown=0.20))
    risk.update_equity(100.0, today="2026-01-01")
    risk.update_equity(89.0, today="2026-01-01")       # 单日 -11% 熔断，但累计回撤仅 -11%
    assert risk.daily_halted and not risk.halted


# ------------------------------------------------ 交易日志
def test_journal_realized_pnl_long_and_short(tmpdir_journal):
    jr = TradeJournal("u1")
    jr.record_fill("buy", "BTC/USDT", 1, 100, 0)
    jr.record_fill("buy", "BTC/USDT", 1, 200, 0)       # 均价 150
    jr.record_fill("sell", "BTC/USDT", 2, 180, 0)      # +2*(180-150)=60
    jr.record_fill("sell", "ETH/USDT", 1, 100, 0)      # 开空
    jr.record_fill("buy", "ETH/USDT", 1, 80, 0)        # +1*(100-80)=20
    s = jr.stats()
    assert s["realized_pnl"] == 80.0
    assert s["closed_trades"] == 2 and s["wins"] == 2 and s["win_rate"] == 100.0


def test_journal_fee_reduces_pnl(tmpdir_journal):
    jr = TradeJournal("u2")
    jr.record_fill("buy", "BTC/USDT", 1, 100, 0)
    jr.record_fill("sell", "BTC/USDT", 1, 110, 2)      # 毛利10 - 手续费2 = 8
    assert jr.stats()["realized_pnl"] == 8.0


def test_journal_restart_continuity(tmpdir_journal):
    jr = TradeJournal("u3")
    jr.record_fill("buy", "BTC/USDT", 1, 100, 0)
    jr.record_fill("sell", "BTC/USDT", 1, 130, 0)
    assert jr.stats()["realized_pnl"] == 30.0
    jr2 = TradeJournal("u3")                            # 重启：不能重复计入
    assert jr2.stats()["realized_pnl"] == 30.0
    assert jr2.stats()["closed_trades"] == 1


def test_max_drawdown_from_equity(tmpdir_journal):
    jr = TradeJournal("u4")
    for e in [100, 120, 90, 110]:                      # 峰值120→谷底90 = -25%
        jr.record_equity(e)
    assert jr.stats()["max_drawdown"] == -25.0


# ------------------------------------------------ 引擎联动
def _engine(strategy, risk=None, **kw):
    return PortfolioEngine(
        mode="safetytest", market="synthetic",
        universe=[f"S{i}" for i in range(10)], strategy=strategy,
        broker=PaperBroker(cash=100_000), risk=risk or RiskManager(RiskConfig()),
        max_positions=5, lookback=200, execute=True, **kw)


def test_engine_writes_live_stats(tmpdir_journal):
    eng = _engine(AllLong())
    st = eng.tick()
    assert "live" in st and "daily_halted" in st and "daily_loss_pct" in st
    assert st["live"]["equity_curve"]                  # 有资金曲线快照
    # 成交被记入日志
    assert (tmpdir_journal / "journal_safetytest.jsonl").exists()


def _open_long(broker, sym, px, amt):
    broker.set_price(sym, px)
    broker.submit(Order(sym, "buy", amt))


def test_stop_loss_force_closes(tmpdir_journal):
    eng = _engine(AllLong(), stop_loss=0.08, take_profit=0.0)
    _open_long(eng.broker, "X", 100, 10)
    acc, held, trades = eng.broker.get_account(), {"X"}, []
    eng._apply_stops({"X": 90}, acc, held, trades)      # -10% < -8%
    assert "X" not in held and "止损" in trades[0]["reason"]


def test_take_profit_force_closes(tmpdir_journal):
    eng = _engine(AllLong(), stop_loss=0.08, take_profit=0.25)
    _open_long(eng.broker, "X", 100, 10)
    acc, held, trades = eng.broker.get_account(), {"X"}, []
    eng._apply_stops({"X": 130}, acc, held, trades)     # +30% > 25%
    assert "X" not in held and "止盈" in trades[0]["reason"]


def test_trailing_stop_closes_after_peak(tmpdir_journal):
    eng = _engine(AllLong(), stop_loss=0.0, take_profit=0.0, trailing_stop=0.05)
    _open_long(eng.broker, "X", 100, 10)
    acc, held, trades = eng.broker.get_account(), {"X"}, []
    eng._apply_stops({"X": 120}, acc, held, trades)     # 峰值 +20%，不平
    assert "X" in held and not trades
    eng._apply_stops({"X": 113}, acc, held, trades)     # 从峰值回撤 ~7% > 5%
    assert "X" not in held and "移动止损" in trades[-1]["reason"]


def test_stop_ratchets_up_and_locks_profit(tmpdir_journal):
    """盈利后止损上移锁盈：涨上去后止损抬到成本上方，回落触发时锁住利润。"""
    eng = _engine(AllLong(), stop_loss=0.08, take_profit=0.0,
                  trailing_stop=0.05, breakeven_trigger=0.05)
    _open_long(eng.broker, "X", 100, 10)
    entry = eng.broker.get_account().positions["X"].avg_price
    acc, held, trades = eng.broker.get_account(), {"X"}, []
    eng._apply_stops({"X": 110}, acc, held, trades)     # 峰值 +10%
    assert eng.stop_level("X", entry, True) > entry     # 止损已上移到成本上方(锁盈)
    assert "X" in held and not trades                   # 还没回落，不平
    eng._apply_stops({"X": 104}, acc, held, trades)     # 回落跌破上移后的止损
    assert "X" not in held and "锁盈" in trades[-1]["reason"]


def test_stops_disabled_hold_through_loss(tmpdir_journal):
    eng = _engine(AllLong(), stop_loss=0.0, take_profit=0.0, trailing_stop=0.0)
    _open_long(eng.broker, "X", 100, 10)
    acc, held, trades = eng.broker.get_account(), {"X"}, []
    eng._apply_stops({"X": 50}, acc, held, trades)      # -50% 但止损关闭
    assert "X" in held and not trades


def test_daily_halt_blocks_new_opens(tmpdir_journal):
    # 用真实当天日期，确保引擎 tick(同日) 不会触发跨日复位
    risk = RiskManager(RiskConfig(max_daily_loss=0.10))
    risk.update_equity(100_000)                        # 当日起点
    risk.update_equity(80_000)                         # -20% → 触发单日熔断
    assert risk.daily_halted
    eng = _engine(AllLong(), risk=risk)
    st = eng.tick()                                    # 引擎同日 tick
    assert risk.daily_halted                           # 同日不复位
    assert len(st["positions"]) == 0                   # 熔断当天不开新仓

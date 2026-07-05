"""保命闭环测试：交易日志(已实现盈亏/重启续接) + 单日亏损熔断 + 引擎联动。"""
from __future__ import annotations

import pandas as pd
import pytest

import qbot.journal as J
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

"""向量化回测引擎。

关键的诚实性设计（决定回测可不可信）：
1. **防未来函数**：t 时刻产生的目标仓位，在 t+1 才成交（next-bar execution）。
2. **手续费**：每次调仓按成交额收取 fee_rate。
3. **滑点**：成交价在不利方向偏移 slippage。
4. 结果同时给出资金曲线、逐笔成交、回撤序列，便于面板可视化和归因。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..strategies.base import Strategy
from .metrics import compute_metrics, trade_stats


@dataclass
class BacktestResult:
    equity: pd.Series
    positions: pd.Series
    trades: pd.DataFrame
    metrics: dict
    price: pd.Series

    def summary(self) -> str:
        m = self.metrics
        lines = [
            f"总收益      : {m.get('total_return', 0):>8.2%}",
            f"年化收益    : {m.get('cagr', 0):>8.2%}",
            f"年化波动    : {m.get('annual_vol', 0):>8.2%}",
            f"夏普        : {m.get('sharpe', 0):>8.2f}",
            f"索提诺      : {m.get('sortino', 0):>8.2f}",
            f"最大回撤    : {m.get('max_drawdown', 0):>8.2%}",
            f"卡玛(年化/回撤): {m.get('calmar', 0):>6.2f}",
            f"交易次数    : {m.get('num_trades', 0):>8d}",
            f"胜率        : {m.get('win_rate', 0):>8.2%}",
            f"盈利因子    : {m.get('profit_factor', 0):>8.2f}",
        ]
        return "\n".join(lines)


class Backtester:
    def __init__(
        self,
        initial_capital: float = 100_000.0,
        fee_rate: float = 0.0005,     # 单边万5，OKX 现货挂单档位量级
        slippage: float = 0.0005,     # 单边万5 滑点，保守起见
        periods_per_year: int = 252,  # 日线股票=252；加密日线可用365
        atr_stop_mult: float | None = None,  # 设为数值(如3.0)即开启 ATR 移动止损
    ):
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.periods_per_year = periods_per_year
        self.atr_stop_mult = atr_stop_mult

    def run(self, df: pd.DataFrame, strategy: Strategy) -> BacktestResult:
        df = df.dropna(subset=["close"]).copy()
        target = strategy.generate_positions(df).fillna(0.0)
        if strategy.long_only:
            target = target.clip(lower=0.0)

        # 可选：叠加 ATR 移动止损（吊灯止损），回撤过大时强制离场
        if self.atr_stop_mult:
            from ..risk.stops import apply_atr_trailing_stop
            target = apply_atr_trailing_stop(df, target, self.atr_stop_mult)

        # 防未来函数：今天收盘算出的目标仓位，明天才执行
        exec_pos = target.shift(1).fillna(0.0)

        close = df["close"]
        ret = close.pct_change().fillna(0.0)

        # 持仓收益 = 昨日仓位 * 今日收益
        strat_ret = exec_pos * ret

        # 换手带来的成本（手续费+滑点），只在仓位变化时发生
        turnover = exec_pos.diff().abs().fillna(exec_pos.abs())
        cost = turnover * (self.fee_rate + self.slippage)
        net_ret = strat_ret - cost

        equity = (1.0 + net_ret).cumprod() * self.initial_capital
        equity.iloc[0] = self.initial_capital

        trades = self._extract_trades(df, exec_pos, close)

        metrics = compute_metrics(equity, self.periods_per_year)
        metrics.update(trade_stats(trades))

        return BacktestResult(
            equity=equity,
            positions=exec_pos,
            trades=trades,
            metrics=metrics,
            price=close,
        )

    @staticmethod
    def _extract_trades(df: pd.DataFrame, pos: pd.Series, close: pd.Series) -> pd.DataFrame:
        """把仓位序列切成一段段"持仓区间"，每段算一笔交易的盈亏。"""
        records = []
        entry_i = None
        p = pos.to_numpy()
        c = close.to_numpy()
        idx = df.index
        for i in range(len(p)):
            if entry_i is None and p[i] > 0:
                entry_i = i
            elif entry_i is not None and p[i] == 0:
                pnl = (c[i] / c[entry_i] - 1.0)
                records.append({
                    "entry_time": idx[entry_i], "exit_time": idx[i],
                    "entry_price": c[entry_i], "exit_price": c[i],
                    "pnl": pnl,
                })
                entry_i = None
        # 收尾未平仓
        if entry_i is not None:
            pnl = (c[-1] / c[entry_i] - 1.0)
            records.append({
                "entry_time": idx[entry_i], "exit_time": idx[-1],
                "entry_price": c[entry_i], "exit_price": c[-1], "pnl": pnl,
            })
        return pd.DataFrame(records)

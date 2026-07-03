"""绩效指标。这些数字决定一个策略能不能碰真钱。

老交易员只看三样东西：最大回撤、夏普、以及"这条曲线我睡得着觉吗"。
年化收益是最会骗人的数字——脱离回撤谈年化都是耍流氓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_metrics(equity: pd.Series, periods_per_year: int = 252) -> dict:
    """输入资金曲线（净值序列），输出一揽子风险收益指标。"""
    equity = equity.dropna()
    if len(equity) < 2:
        return {}

    rets = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    years = len(equity) / periods_per_year
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0

    vol = rets.std() * np.sqrt(periods_per_year)
    sharpe = (rets.mean() * periods_per_year) / (rets.std() * np.sqrt(periods_per_year)) \
        if rets.std() > 0 else 0.0
    downside = rets[rets < 0].std() * np.sqrt(periods_per_year)
    sortino = (rets.mean() * periods_per_year) / downside if downside > 0 else 0.0

    # 最大回撤
    roll_max = equity.cummax()
    drawdown = equity / roll_max - 1.0
    max_dd = drawdown.min()
    # Calmar：年化 / 最大回撤，衡量"收益是否配得上你承受的痛苦"
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    return {
        "total_return": float(total_return),
        "cagr": float(cagr),
        "annual_vol": float(vol),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": float(max_dd),
        "calmar": float(calmar),
    }


def trade_stats(trades: pd.DataFrame) -> dict:
    """从成交记录算胜率、盈亏比、盈利因子。"""
    if trades is None or trades.empty:
        return {"num_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_pnl": 0.0}
    pnl = trades["pnl"]
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    win_rate = len(wins) / len(pnl) if len(pnl) else 0.0
    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    return {
        "num_trades": int(len(pnl)),
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "avg_pnl": float(pnl.mean()),
    }

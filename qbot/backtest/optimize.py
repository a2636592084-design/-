"""参数优化 + 样本外(walk-forward)验证。

⚠️ 为什么优化目标不是"胜率"？
   把参数拟合到历史最高胜率，是量化最经典的自杀方式（过拟合）。
   我们优化的是 **卡玛比率(年化/最大回撤)** 或 **夏普**——奖励"稳"，惩罚"大回撤"。

walk-forward 思想：只用过去调参，永远在"没见过的未来段"上打分。
   若 样本内(IS)很好、样本外(OOS)崩了 => 这是过拟合，别信这套参数。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import pandas as pd

from ..strategies.base import Strategy
from .engine import Backtester

# 每个策略的候选参数网格（小而精，网格越大越容易过拟合）
PARAM_GRIDS: dict[str, dict[str, list]] = {
    "ma_cross": {"fast": [10, 20, 30], "slow": [50, 60, 100]},
    "donchian": {"entry": [20, 30, 55], "exit": [10, 15, 20]},
    "trend_stack": {"fast": [10, 20], "adx_min": [20, 25, 30]},
    "supertrend": {"period": [7, 10, 14], "mult": [2.0, 3.0, 4.0]},
    "rsi_reversion": {"oversold": [25, 30, 35], "exit_level": [50, 55, 60]},
    "bollinger_reversion": {"adx_max": [18, 20, 25], "b_low": [0.02, 0.05, 0.1]},
    "vwap_momentum": {"vwma_n": [10, 20, 30]},
    "regime_switch": {"adx_trend": [22, 25, 28], "adx_range": [18, 20]},
}


def _param_combos(grid: dict[str, list]) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*grid.values())]


def _score(df: pd.DataFrame, cls, params: dict, objective: str, ppy: int,
           atr_stop: float | None) -> float:
    m = Backtester(periods_per_year=ppy, atr_stop_mult=atr_stop).run(df, cls(**params)).metrics
    val = m.get(objective, 0.0)
    return val if val == val else -1e9   # NaN -> 极差


@dataclass
class WFFold:
    fold: int
    best_params: dict
    is_score: float
    oos_score: float
    oos_metrics: dict


def walk_forward(
    df: pd.DataFrame,
    cls: type[Strategy],
    grid: dict[str, list],
    n_splits: int = 4,
    objective: str = "calmar",
    ppy: int = 252,
    atr_stop: float | None = None,
) -> list[WFFold]:
    """扩展窗口 walk-forward：第 k 折用前 k 段调参、第 k+1 段做样本外检验。"""
    combos = _param_combos(grid)
    seg = len(df) // (n_splits + 1)
    folds: list[WFFold] = []
    for k in range(1, n_splits + 1):
        train = df.iloc[: seg * k]
        test = df.iloc[seg * k : seg * (k + 1)]
        if len(train) < 60 or len(test) < 30:
            continue
        best = max(combos, key=lambda p: _score(train, cls, p, objective, ppy, atr_stop))
        is_score = _score(train, cls, best, objective, ppy, atr_stop)
        res = Backtester(periods_per_year=ppy, atr_stop_mult=atr_stop).run(test, cls(**best))
        folds.append(WFFold(k, best, is_score, res.metrics.get(objective, 0.0), res.metrics))
    return folds

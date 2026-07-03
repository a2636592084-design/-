"""RSI 均值回归。超卖买入、回归卖出，适合震荡市。

和趋势策略互补：一个吃趋势，一个吃震荡。
真实组合里往往两类策略配着用，平滑资金曲线。
"""
from __future__ import annotations

import pandas as pd

from .base import Strategy
from .indicators import rsi


class RSIReversionStrategy(Strategy):
    name = "rsi_reversion"

    def __init__(self, period: int = 14, oversold: float = 30, exit_level: float = 55):
        super().__init__(period=period, oversold=oversold, exit_level=exit_level)
        self.period = period
        self.oversold = oversold
        self.exit_level = exit_level

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        r = rsi(df["close"], self.period)
        pos = pd.Series(0.0, index=df.index)
        holding = 0.0
        # 状态机：超卖进场，回到 exit_level 出场。用 numpy 迭代保证无未来函数。
        vals = r.to_numpy()
        out = pos.to_numpy().copy()
        for i in range(len(vals)):
            v = vals[i]
            if v != v:  # NaN
                out[i] = 0.0
                continue
            if holding == 0.0 and v < self.oversold:
                holding = 1.0
            elif holding == 1.0 and v > self.exit_level:
                holding = 0.0
            out[i] = holding
        return pd.Series(out, index=df.index)

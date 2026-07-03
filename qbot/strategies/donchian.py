"""唐奇安通道突破。海龟交易法的核心，趋势跟踪的另一种形态。

逻辑：突破 N 日最高 => 做多；跌破 M 日最低 => 平仓。
经得起几十年检验的老策略，适合加密这种趋势明显的品种。
"""
from __future__ import annotations

import pandas as pd

from .base import Strategy


class DonchianBreakoutStrategy(Strategy):
    name = "donchian"

    def __init__(self, entry: int = 20, exit: int = 10):
        super().__init__(entry=entry, exit=exit)
        self.entry = entry
        self.exit = exit

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        # 用 shift(1) 避免"用当根K线的最高价判断当根突破"的未来函数
        upper = high.rolling(self.entry).max().shift(1)
        lower = low.rolling(self.exit).min().shift(1)

        pos = pd.Series(0.0, index=df.index)
        holding = 0.0
        c = close.to_numpy()
        u = upper.to_numpy()
        lo = lower.to_numpy()
        out = pos.to_numpy().copy()
        for i in range(len(c)):
            if u[i] != u[i] or lo[i] != lo[i]:
                out[i] = holding
                continue
            if holding == 0.0 and c[i] > u[i]:
                holding = 1.0
            elif holding == 1.0 and c[i] < lo[i]:
                holding = 0.0
            out[i] = holding
        return pd.Series(out, index=df.index)

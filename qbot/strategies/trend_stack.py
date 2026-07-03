"""趋势栈策略：EMA 多周期骨架 + ADX 过滤 + MACD 动量触发。

这是把你说的三件事拼起来的标准趋势系统：
  · EMA20 > EMA50 > EMA200  —— 多头排列，趋势方向骨架（前提过滤器）
  · ADX > adx_min           —— 有真趋势才做，震荡直接屏蔽（防亏死）
  · MACD 柱体 > 0           —— 动量向上确认，作为进场触发
三个条件同时满足才满仓做多，任一失效即空仓。宁可错过，不可做错。
"""
from __future__ import annotations

import pandas as pd

from .base import Strategy
from .indicators import adx, ema, macd


class TrendStackStrategy(Strategy):
    name = "trend_stack"

    def __init__(self, fast: int = 20, mid: int = 50, slow: int = 200,
                 adx_min: float = 25.0):
        super().__init__(fast=fast, mid=mid, slow=slow, adx_min=adx_min)
        self.fast, self.mid, self.slow, self.adx_min = fast, mid, slow, adx_min

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        e_fast, e_mid, e_slow = ema(close, self.fast), ema(close, self.mid), ema(close, self.slow)
        adx_, _, _ = adx(df)
        _, _, hist = macd(close)

        aligned = (e_fast > e_mid) & (e_mid > e_slow)   # 多头排列
        trending = adx_ > self.adx_min                   # 有趋势
        momentum = hist > 0                              # 动量向上

        pos = (aligned & trending & momentum).astype(float)
        pos[e_slow.isna() | adx_.isna() | hist.isna()] = 0.0
        return pos

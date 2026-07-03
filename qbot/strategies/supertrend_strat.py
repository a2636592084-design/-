"""超级趋势策略：SuperTrend 作为趋势方向 + 移动止损，ADX 过滤震荡。

SuperTrend 本身就是 ATR + 趋势方向的工程化合体——它给出的线天然可当移动止损。
逻辑：
  · SuperTrend 方向 = +1（多头，线在价格下方）→ 持有
  · 叠加 ADX 过滤：ADX<adx_min 时不进场，避免震荡市被通道来回甩
  · 方向翻转为 -1 → 立即离场（这就是内置的追踪止损）
"""
from __future__ import annotations

import pandas as pd

from .base import Strategy
from .indicators import adx, supertrend


class SuperTrendStrategy(Strategy):
    name = "supertrend"

    def __init__(self, period: int = 10, mult: float = 3.0, adx_min: float = 20.0):
        super().__init__(period=period, mult=mult, adx_min=adx_min)
        self.period, self.mult, self.adx_min = period, mult, adx_min

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        _, direction = supertrend(df, self.period, self.mult)
        adx_, _, _ = adx(df)
        pos = ((direction > 0) & (adx_ > self.adx_min)).astype(float)
        pos[adx_.isna()] = 0.0
        return pos

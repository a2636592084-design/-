"""市场状态自适应策略（ADX 路由器）：趋势市跑趋势、震荡市跑回归。

这是"多策略组合"的核心思想，也是平滑资金曲线最有效的一招：
  · ADX > adx_trend（如 25）→ 处于趋势市 → 用 trend_stack（EMA+ADX+MACD）
  · ADX < adx_range（如 20）→ 处于震荡市 → 用 bollinger_reversion（%B+StochRSI）
  · 中间灰区(20~25) → 观望空仓，不在模糊地带下注

因为两个子策略各自已按 ADX 自我门控（趋势要 ADX 高、回归要 ADX 低），
二者天然互斥、绝不同时开仓——一个吃趋势、一个吃震荡，错峰工作、互相补台。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .bollinger_reversion import BollingerReversionStrategy
from .indicators import adx
from .trend_stack import TrendStackStrategy


class RegimeSwitchStrategy(Strategy):
    name = "regime_switch"

    def __init__(self, adx_trend: float = 25.0, adx_range: float = 20.0):
        super().__init__(adx_trend=adx_trend, adx_range=adx_range)
        self.adx_trend = adx_trend
        self.adx_range = adx_range
        self.trend = TrendStackStrategy(adx_min=adx_trend)
        self.range = BollingerReversionStrategy(adx_max=adx_range)

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        adx_, _, _ = adx(df)
        trend_pos = self.trend.generate_positions(df).to_numpy()
        range_pos = self.range.generate_positions(df).to_numpy()
        a = adx_.to_numpy()

        out = np.zeros(len(df))
        trending = a > self.adx_trend
        ranging = a < self.adx_range
        out[trending] = trend_pos[trending]
        out[ranging] = range_pos[ranging]
        return pd.Series(out, index=df.index)

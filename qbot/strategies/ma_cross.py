"""双均线趋势跟踪。经典、稳健、可解释——量化入门第一课。

逻辑：快线上穿慢线 => 做多；下穿 => 平仓。
趋势行情赚大钱，震荡行情靠止损和小仓位活下来。
"""
from __future__ import annotations

import pandas as pd

from .base import Strategy
from .indicators import sma


class MACrossStrategy(Strategy):
    name = "ma_cross"

    def __init__(self, fast: int = 20, slow: int = 60):
        super().__init__(fast=fast, slow=slow)
        self.fast = fast
        self.slow = slow

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        f = sma(close, self.fast)
        s = sma(close, self.slow)
        pos = (f > s).astype(float)   # 快在慢上 => 满仓；否则空仓
        pos[f.isna() | s.isna()] = 0.0
        return pos

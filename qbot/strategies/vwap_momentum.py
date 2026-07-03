"""VWAP 日内动量：价格在机构成本线之上 + 量能配合 = 多头占优。

VWAP 是日内基准，请在 15m / 1h 等日内周期上使用（日线上 VWAP 无意义）。
逻辑：
  · 收盘价 > VWAP（站上机构成本线，多头占优）
  · OBV 上升（真实量能推动，过滤无量假突破）
  · VWMA 上行（量加权成本走高，确认趋势）
三者共振才持有，跌破 VWAP 立即离场。
"""
from __future__ import annotations

import pandas as pd

from .base import Strategy
from .indicators import obv, vwap, vwma


class VWAPMomentumStrategy(Strategy):
    name = "vwap_momentum"

    def __init__(self, vwma_n: int = 20, reset: str = "D"):
        super().__init__(vwma_n=vwma_n, reset=reset)
        self.vwma_n, self.reset = vwma_n, reset

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        vw = vwap(df, reset=self.reset)
        ob = obv(close, df["volume"])
        vm = vwma(close, df["volume"], self.vwma_n)

        above_vwap = close > vw
        vol_push = ob >= ob.shift(1)
        vwma_up = vm >= vm.shift(1)

        pos = (above_vwap & vol_push & vwma_up).astype(float)
        pos[vw.isna() | vm.isna()] = 0.0
        return pos

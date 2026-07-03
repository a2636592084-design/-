"""布林带均值回归：只在震荡市(ADX<20)做，超卖买入、回归中轨卖出。

和趋势策略严格互补——趋势策略在 ADX>阈值 时工作，本策略在 ADX<阈值 时工作，
两者天然错开，避免在同一行情里互相打架。
逻辑（状态机，防未来函数）：
  进场：ADX<adx_max（确认震荡） 且 %B<b_low（跌破下轨附近） 且 StochRSI 超卖
        且 OBV 未破位（量价不背离，说明是回调而非崩塌）
  出场：%B 回到中轨(0.5) 以上，或 StochRSI 转入超买
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import adx, bollinger, obv, stoch_rsi


class BollingerReversionStrategy(Strategy):
    name = "bollinger_reversion"

    def __init__(self, n: int = 20, mult: float = 2.0, adx_max: float = 20.0,
                 b_low: float = 0.05, srsi_os: float = 20.0, srsi_ob: float = 80.0):
        super().__init__(n=n, mult=mult, adx_max=adx_max,
                         b_low=b_low, srsi_os=srsi_os, srsi_ob=srsi_ob)
        self.n, self.mult, self.adx_max = n, mult, adx_max
        self.b_low, self.srsi_os, self.srsi_ob = b_low, srsi_os, srsi_ob

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        _, _, _, pct_b, _ = bollinger(close, self.n, self.mult)
        k, _ = stoch_rsi(close, self.n)
        adx_, _, _ = adx(df)
        ob = obv(close, df["volume"])
        ob_rising = ob >= ob.shift(1)   # 量价不背离（下跌途中量能未持续走弱）

        pb = pct_b.to_numpy()
        kk = k.to_numpy()
        ax = adx_.to_numpy()
        obr = ob_rising.to_numpy()
        out = np.zeros(len(close))
        holding = 0.0
        for i in range(len(close)):
            if np.isnan(pb[i]) or np.isnan(kk[i]) or np.isnan(ax[i]):
                out[i] = holding
                continue
            if holding == 0.0:
                if ax[i] < self.adx_max and pb[i] < self.b_low \
                        and kk[i] < self.srsi_os and obr[i]:
                    holding = 1.0
            else:
                if pb[i] > 0.5 or kk[i] > self.srsi_ob:
                    holding = 0.0
            out[i] = holding
        return pd.Series(out, index=df.index)

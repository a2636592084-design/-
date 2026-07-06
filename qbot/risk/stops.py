"""ATR 移动止损（吊灯止损 Chandelier Exit）。

你清单里的铁律："ATR 是止损与仓位计算的唯一锚点，没有它开仓就是赌博。"
本模块把它做成一个可复用的因果函数：给定策略的原始持仓序列，用 ATR 追踪止损
在价格回撤过大时强制离场。只用当前及过去数据，绝不偷看未来。

吊灯止损逻辑：持多单期间，记录进场以来的最高价 peak，
止损线 = peak - mult × ATR，且只上移不下移；收盘跌破止损线即离场。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategies.indicators import atr


def apply_atr_trailing_stop(
    df: pd.DataFrame,
    positions: pd.Series,
    mult: float = 3.0,
    atr_n: int = 14,
) -> pd.Series:
    """在原始持仓上叠加 ATR 追踪止损，返回修正后的持仓序列（仅多头）。"""
    a = atr(df, atr_n).to_numpy()
    close = df["close"].to_numpy()
    pos = positions.to_numpy()
    out = np.zeros(len(pos))

    in_pos = False
    blocked = False   # 因价格止损离场后，锁定到信号归零前不再进场（不抄底）
    peak = 0.0
    trail = -np.inf
    for i in range(len(pos)):
        if pos[i] <= 0:            # 策略本身已空仓 → 解除锁定，重置状态
            in_pos = False
            blocked = False
            out[i] = 0.0
            continue
        if blocked:               # 信号仍为多头，但上次是被止损打出来的 → 继续观望
            out[i] = 0.0
            continue
        if not in_pos:
            if not np.isnan(a[i]):   # 进场并初始化止损线
                in_pos = True
                peak = close[i]
                trail = peak - mult * a[i]
                out[i] = pos[i]
            else:
                out[i] = 0.0
        else:
            peak = max(peak, close[i])
            trail = max(trail, peak - mult * a[i])   # 止损线只上移
            if close[i] < trail:                      # 跌破止损 → 离场并锁定
                in_pos = False
                blocked = True
                out[i] = 0.0
            else:
                out[i] = pos[i]
    return pd.Series(out, index=df.index)

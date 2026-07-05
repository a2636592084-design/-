"""大盘方向闸（market regime gate）：只在大盘(BTC)自己有明确趋势时才开新仓。

诚实的动机（不是拟合噪声，是常识）：
  币圈约 70% 时间在震荡。趋势策略在震荡里就是反复被打脸+送手续费——回撤主要来自这里。
  所以：**大盘横盘时整套策略休息、不开新仓；大盘上行只做多、下行只做空**，让每一笔
  开仓都顺着大盘的潮水，而不是逆着拍。已有持仓的止损/信号平仓照常，闸只管"要不要新开"。

判定（纯因果，只用到每根K线当时已知的信息，配合回测 shift(1) 无未来函数）：
  · ADX < adx_min          → 0 观望（无趋势/震荡，谁都不开）
  · ADX≥闸 且 收盘>EMA(n)   → +1 多头行情（只允许做多）
  · ADX≥闸 且 收盘<EMA(n)   → -1 空头行情（只允许做空）
"""
from __future__ import annotations

import pandas as pd

from .indicators import adx as _adx
from .indicators import ema as _ema


def regime_series(df: pd.DataFrame, adx_min: float = 20.0, ema_len: int = 200) -> pd.Series:
    """逐根K线的大盘态：+1 多头 / -1 空头 / 0 震荡观望。因果、可 shift。"""
    adx_val = _adx(df)[0]
    ema_n = _ema(df["close"], ema_len)
    up = df["close"] > ema_n
    strong = adx_val >= adx_min
    reg = pd.Series(0, index=df.index, dtype="int8")
    reg[strong & up] = 1
    reg[strong & ~up] = -1
    return reg


def regime_now(df: pd.DataFrame, adx_min: float = 20.0, ema_len: int = 200) -> int:
    """最新一根的大盘态（实盘引擎每轮用它决定这一轮能不能开、开多还是开空）。"""
    if df is None or len(df) < ema_len // 2:
        return 1     # 数据太短无法判定 → 不拦（退回到"照常开仓"，由个股信号自己把关）
    s = regime_series(df, adx_min=adx_min, ema_len=ema_len)
    v = s.iloc[-1]
    return int(v) if v == v else 0

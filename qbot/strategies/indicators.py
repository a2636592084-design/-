"""技术指标库（纯 pandas/numpy，不依赖 TA-Lib）。

设计哲学（按资深交易员的取舍，同类只留一个，宁缺毋滥防过拟合）：
  ① EMA 多周期(20/50/200) —— 趋势方向骨架，所有信号的前提过滤器
  ② ATR —— 止损与仓位计算的唯一锚点
  ③ MACD —— 趋势动量信号源（金叉 + 柱体扩张）
  ④ RSI / Stoch RSI —— 动量超买超卖 + 背离；Stoch RSI 更灵敏适合短周期
  ⑤ 布林带 —— 波动率通道 + 均值回归（%B 与带宽配合）
  ⑥ VWAP —— 机构成本线，日内量化基准
  ⑦ ADX —— <20 直接屏蔽趋势信号，防震荡市亏死的关键过滤器
  ⑧ OBV —— 量价背离，识别无量假突破
  ⑨ SuperTrend —— ATR + 趋势方向的工程化合体，天然的止损追踪线
  ⑩ Volume Profile(POC) / VWMA —— 筹码密集区 / 量加权成本，关键支撑阻力

所有指标只用"当前及过去"的数据（因果），配合回测引擎的 shift(1) 执行，
保证不出现未来函数。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 均线族
def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def vwma(price: pd.Series, volume: pd.Series, n: int = 20) -> pd.Series:
    """量加权移动均线：成交量大的价格权重更高，比 SMA 更贴近机构成本。"""
    pv = (price * volume).rolling(n, min_periods=n).sum()
    v = volume.rolling(n, min_periods=n).sum()
    return pv / v


# ---------------------------------------------------------------- 波动率
def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, prev_close = df["high"], df["low"], df["close"].shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """真实波动幅度均值（Wilder 平滑）。止损/仓位的锚点。"""
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


# ---------------------------------------------------------------- 动量
def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (macd线, 信号线, 柱体)。柱体>0 且扩张 = 多头动量增强。"""
    macd_line = ema(s, fast) - ema(s, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def stoch_rsi(s: pd.Series, n: int = 14, k: int = 3, d: int = 3):
    """Stoch RSI：对 RSI 再做随机指标，更灵敏，适合短周期。返回 (%K, %D)，范围 0~100。"""
    r = rsi(s, n)
    lo = r.rolling(n, min_periods=n).min()
    hi = r.rolling(n, min_periods=n).max()
    stoch = (r - lo) / (hi - lo).replace(0, np.nan)
    k_line = (stoch * 100).rolling(k, min_periods=k).mean()
    d_line = k_line.rolling(d, min_periods=d).mean()
    return k_line, d_line


# ---------------------------------------------------------------- 通道
def bollinger(s: pd.Series, n: int = 20, mult: float = 2.0):
    """布林带。返回 (中轨, 上轨, 下轨, %B, 带宽)。
    %B<0 价格跌破下轨(超卖)，>1 突破上轨(超买)；带宽扩张=波动放大。"""
    mid = sma(s, n)
    std = s.rolling(n, min_periods=n).std(ddof=0)
    upper = mid + mult * std
    lower = mid - mult * std
    pct_b = (s - lower) / (upper - lower).replace(0, np.nan)
    bandwidth = (upper - lower) / mid
    return mid, upper, lower, pct_b, bandwidth


# ---------------------------------------------------------------- 趋势强度
def adx(df: pd.DataFrame, n: int = 14):
    """平均趋向指数。返回 (adx, +DI, -DI)。
    ADX<20 视为无趋势（震荡），此时应屏蔽所有趋势跟踪信号。"""
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    atr_ = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return adx_, plus_di, minus_di


# ---------------------------------------------------------------- 量价
def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """能量潮：价涨加量、价跌减量的累计。价创新高但 OBV 不创新高 = 顶背离。"""
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def vwap(df: pd.DataFrame, reset: str | None = "D") -> pd.Series:
    """成交量加权平均价（机构成本线）。
    reset='D' 每个自然日重置（日内量化标准用法）；reset=None 从头累计（锚定 VWAP）。
    注意：VWAP 本质是日内基准，请在 15m/1h 等日内周期上使用才有意义。"""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"]
    if reset:
        key = df.index.normalize()
        cum_tpv = (tp * vol).groupby(key).cumsum()
        cum_v = vol.groupby(key).cumsum()
    else:
        cum_tpv = (tp * vol).cumsum()
        cum_v = vol.cumsum()
    return cum_tpv / cum_v.replace(0, np.nan)


# ---------------------------------------------------------------- 工程化趋势
def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0):
    """SuperTrend：ATR 通道 + 趋势翻转。返回 (supertrend线, 方向)。
    方向 +1=上涨(线在价格下方，作为移动止损)，-1=下跌。"""
    hl2 = (df["high"] + df["low"]) / 2
    atr_ = atr(df, n)
    upper = hl2 + mult * atr_
    lower = hl2 - mult * atr_

    close = df["close"].to_numpy()
    up = upper.to_numpy()
    lo = lower.to_numpy()
    n_len = len(close)
    st = np.full(n_len, np.nan)
    direction = np.ones(n_len)  # 1=多, -1=空

    final_up = up.copy()
    final_lo = lo.copy()
    for i in range(1, n_len):
        if np.isnan(up[i]) or np.isnan(lo[i]):
            continue
        # 收紧的通道（防止在盘整中通道来回甩）
        final_up[i] = min(up[i], final_up[i - 1]) if close[i - 1] <= final_up[i - 1] else up[i]
        final_lo[i] = max(lo[i], final_lo[i - 1]) if close[i - 1] >= final_lo[i - 1] else lo[i]
        # 翻转判断
        if close[i] > final_up[i - 1]:
            direction[i] = 1
        elif close[i] < final_lo[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        st[i] = final_lo[i] if direction[i] == 1 else final_up[i]

    return (
        pd.Series(st, index=df.index),
        pd.Series(direction, index=df.index),
    )


def volume_profile_poc(df: pd.DataFrame, window: int = 60, bins: int = 24) -> pd.Series:
    """滚动 Volume Profile 的 POC（控制点：成交量最密集的价位）。
    作为动态支撑/阻力。窗口内把价格分桶、按成交量加权，取量最大的桶中值。"""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    vol = df["volume"].to_numpy()
    n_len = len(close)
    poc = np.full(n_len, np.nan)
    for i in range(window - 1, n_len):
        s = i - window + 1
        p_lo, p_hi = low[s:i + 1].min(), high[s:i + 1].max()
        if p_hi <= p_lo:
            poc[i] = close[i]
            continue
        edges = np.linspace(p_lo, p_hi, bins + 1)
        idx = np.clip(np.digitize(close[s:i + 1], edges) - 1, 0, bins - 1)
        vol_by_bin = np.zeros(bins)
        np.add.at(vol_by_bin, idx, vol[s:i + 1])
        b = int(vol_by_bin.argmax())
        poc[i] = (edges[b] + edges[b + 1]) / 2
    return pd.Series(poc, index=df.index)

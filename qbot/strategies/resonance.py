"""共振面板：多指标"投票"聚合（供行情终端的多空比条 + 逐指标明细）。

诚实说明（很重要）：
- 这是**展示型面板**，把 ~19 个常见指标各自对"当前这一根"的看多/看空/中性投票汇总成一个
  多空比，帮你一眼看清市场共识强弱。**指标多 ≠ 策略更好**——真正下单用的仍是更克制的
  ConfluenceStrategy（6 票加权，防过拟合）。这个面板是"民意调查"，不是"交易信号"。
- 每个指标只用当前及过去数据（因果）。中性(0)票表示该指标此刻方向不明。

分组：趋势 / 动量 / 量能，对应参考站的标签。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import (
    adx,
    bollinger,
    ema,
    macd,
    obv,
    rsi,
    sma,
    supertrend,
    vwap,
    vwma,
)

# 面板指标清单：(key, 中文名, 分组)
INDICATORS: list[tuple[str, str, str]] = [
    ("ma", "MA 均线共振", "趋势"),
    ("macd", "MACD 共振", "趋势"),
    ("boll", "BOLL 中轨共振", "趋势"),
    ("sar", "SAR 抛物线共振", "趋势"),
    ("supertrend", "SUPERTREND 共振", "趋势"),
    ("ichimoku", "一目均衡表 TK 共振", "趋势"),
    ("dmi", "DMI 共振", "趋势"),
    ("aroon", "AROON 阿隆共振", "趋势"),
    ("rsi", "RSI 共振", "动量"),
    ("kdj", "KDJ 共振", "动量"),
    ("cci", "CCI 共振", "动量"),
    ("bias", "BIAS 乖离共振", "动量"),
    ("sroc", "SROC 平滑变动率共振", "动量"),
    ("trix", "TRIX 平滑动量共振", "动量"),
    ("obv", "OBV 量能共振", "量能"),
    ("mfi", "MFI 资金流共振", "量能"),
    ("cmf", "CMF 蔡金资金流共振", "量能"),
    ("vwap", "VWAP 成本锚共振", "量能"),
    ("vwma", "VWMA 量价均线共振", "量能"),
]


def _sign(x: float, dead: float = 0.0) -> int:
    if x != x:                       # NaN
        return 0
    if x > dead:
        return 1
    if x < -dead:
        return -1
    return 0


# ---------------------------------------------------------------- 补充指标
def _parabolic_sar(df: pd.DataFrame, af0=0.02, step=0.02, af_max=0.2) -> pd.Series:
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    sar = np.full(n, np.nan)
    if n < 2:
        return pd.Series(sar, index=df.index)
    trend = 1
    af = af0
    ep = high[0]
    sar[0] = low[0]
    for i in range(1, n):
        sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
        if trend == 1:
            sar[i] = min(sar[i], low[i - 1], low[max(i - 2, 0)])
            if high[i] > ep:
                ep = high[i]
                af = min(af + step, af_max)
            if low[i] < sar[i]:
                trend, sar[i], ep, af = -1, ep, low[i], af0
        else:
            sar[i] = max(sar[i], high[i - 1], high[max(i - 2, 0)])
            if low[i] < ep:
                ep = low[i]
                af = min(af + step, af_max)
            if high[i] > sar[i]:
                trend, sar[i], ep, af = 1, ep, high[i], af0
    return pd.Series(sar, index=df.index)


def _kdj(df: pd.DataFrame, n=9, k=3, d=3):
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k_line = rsv.ewm(com=k - 1, adjust=False).mean()
    d_line = k_line.ewm(com=d - 1, adjust=False).mean()
    return k_line, d_line


def _cci(df: pd.DataFrame, n=20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(n, min_periods=n).mean()
    md = (tp - ma).abs().rolling(n, min_periods=n).mean()
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def _bias(close: pd.Series, n=20) -> pd.Series:
    m = sma(close, n)
    return (close - m) / m * 100


def _aroon(df: pd.DataFrame, n=25):
    up = df["high"].rolling(n + 1, min_periods=n + 1).apply(
        lambda x: 100 * (np.argmax(x)) / n, raw=True)
    dn = df["low"].rolling(n + 1, min_periods=n + 1).apply(
        lambda x: 100 * (np.argmin(x)) / n, raw=True)
    return up, dn


def _mfi(df: pd.DataFrame, n=14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mf = tp * df["volume"]
    pos = mf.where(tp > tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    neg = mf.where(tp < tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    mr = pos / neg.replace(0, np.nan)
    return 100 - 100 / (1 + mr)


def _sroc(close: pd.Series, ema_n=13, roc_n=21) -> pd.Series:
    sm = ema(close, ema_n)
    return (sm / sm.shift(roc_n) - 1.0) * 100


def _trix(close: pd.Series, n=15) -> pd.Series:
    e1 = ema(close, n)
    e2 = ema(e1, n)
    e3 = ema(e2, n)
    return (e3 / e3.shift(1) - 1.0) * 100


def _cmf(df: pd.DataFrame, n=20) -> pd.Series:
    hl = (df["high"] - df["low"]).replace(0, np.nan)
    mfv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl * df["volume"]
    return mfv.rolling(n, min_periods=n).sum() / \
        df["volume"].rolling(n, min_periods=n).sum().replace(0, np.nan)


# ---------------------------------------------------------------- 各指标投票
def _votes_raw(df: pd.DataFrame) -> dict[str, int]:
    close = df["close"]
    price = float(close.iloc[-1])
    v: dict[str, int] = {}

    e20, e50 = ema(close, 20), ema(close, 50)
    v["ma"] = 1 if (price > e20.iloc[-1] > e50.iloc[-1]) else \
        (-1 if (price < e20.iloc[-1] < e50.iloc[-1]) else 0)

    _, _, hist = macd(close)
    v["macd"] = _sign(float(hist.iloc[-1]))

    mid = sma(close, 20)
    v["boll"] = _sign(price - float(mid.iloc[-1]))

    sar = _parabolic_sar(df)
    v["sar"] = _sign(price - float(sar.iloc[-1]))

    _, st_dir = supertrend(df)
    v["supertrend"] = int(np.sign(st_dir.iloc[-1])) or 0

    tenkan = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    kijun = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    tk, kj = float(tenkan.iloc[-1]), float(kijun.iloc[-1])
    v["ichimoku"] = 1 if (tk == tk and price > kj and tk > kj) else \
        (-1 if (tk == tk and price < kj and tk < kj) else 0)

    _, plus_di, minus_di = adx(df)
    v["dmi"] = _sign(float(plus_di.iloc[-1]) - float(minus_di.iloc[-1]))

    au, ad = _aroon(df)
    v["aroon"] = _sign(float(au.iloc[-1]) - float(ad.iloc[-1])) \
        if au.iloc[-1] == au.iloc[-1] else 0

    r = float(rsi(close).iloc[-1])
    v["rsi"] = 1 if r > 55 else (-1 if r < 45 else 0)

    k_line, d_line = _kdj(df)
    v["kdj"] = _sign(float(k_line.iloc[-1]) - float(d_line.iloc[-1]))

    c = float(_cci(df).iloc[-1]) if len(df) > 20 else float("nan")
    v["cci"] = 1 if c > 100 else (-1 if c < -100 else 0)

    v["bias"] = _sign(float(_bias(close).iloc[-1]), dead=0.2)

    v["sroc"] = _sign(float(_sroc(close).iloc[-1]))

    v["trix"] = _sign(float(_trix(close).iloc[-1]))

    ob = obv(close, df["volume"])
    v["obv"] = _sign(float(ob.iloc[-1]) - float(ob.iloc[-6])) if len(ob) > 6 else 0

    m = float(_mfi(df).iloc[-1])
    v["mfi"] = 1 if m > 55 else (-1 if m < 45 else 0)

    v["cmf"] = _sign(float(_cmf(df).iloc[-1]))

    va = vwap(df, reset=None)             # 锚定 VWAP，跨周期都有意义
    v["vwap"] = _sign(price - float(va.iloc[-1]))

    vm = vwma(close, df["volume"], 20)
    v["vwma"] = _sign(price - float(vm.iloc[-1])) if vm.iloc[-1] == vm.iloc[-1] else 0

    return v


def resonance_votes(df: pd.DataFrame) -> dict:
    """返回全部指标投票明细 + 多空/中性计数（前端据此画多空比条与明细）。"""
    if len(df) < 30:
        return {"error": "K线太少，无法计算共振（建议≥60根）。"}
    raw = _votes_raw(df)
    items = [{"key": k, "name": name, "group": grp, "vote": int(raw.get(k, 0))}
             for k, name, grp in INDICATORS]
    bull = sum(1 for it in items if it["vote"] > 0)
    bear = sum(1 for it in items if it["vote"] < 0)
    neutral = len(items) - bull - bear
    return {"indicators": items, "bull": bull, "bear": bear,
            "neutral": neutral, "total": len(items)}

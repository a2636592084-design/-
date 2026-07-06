"""行情终端的"结构化分析"——用现有指标算出 阻力/支撑/止损/分维度评分/文字解读。

诚实：这不是"AI"，是**规则模板**基于技术指标生成。将来接大模型只需替换 text 部分。
共振投票直接复用 ConfluenceStrategy.explain()。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategies.confluence import ConfluenceStrategy
from ..strategies.indicators import atr, bollinger, ema, macd, rsi, sma


def _swing_levels(df: pd.DataFrame, lookback: int = 60):
    """近端摆动高/低作为阻力/支撑（回看窗口内的高低点）。"""
    seg = df.tail(lookback)
    price = float(df["close"].iloc[-1])
    highs = seg["high"]
    lows = seg["low"]
    res = float(highs[highs > price].min()) if (highs > price).any() else float(highs.max())
    sup = float(lows[lows < price].max()) if (lows < price).any() else float(lows.min())
    return res, sup


def _dim(score: float) -> int:
    """把 [-1,1] 的分量映射到 0-100（50 中性）。"""
    return int(round((score + 1) / 2 * 100))


def analyze(df: pd.DataFrame) -> dict:
    """返回结构化分析：现价/阻力/支撑/止损 + 分维度评分 + 共振投票 + 文字解读。"""
    close = df["close"]
    price = float(close.iloc[-1])
    a = float(atr(df).iloc[-1]) if len(df) > 15 else price * 0.02

    conf = ConfluenceStrategy()
    ex = conf.explain(df)                       # 复用共振投票 + 共振分
    score = float(ex["score"])                  # [-1,1]
    votes = ex["votes"]
    bull = score >= 0

    res, sup = _swing_levels(df)
    # 止损：多头偏向放价下方、空头偏向放价上方，用 2×ATR
    stop = round(price - 2 * a, 4) if bull else round(price + 2 * a, 4)

    # 分维度评分（趋势/均线/动量/量能）
    e20, e50, e200 = ema(close, 20), ema(close, 50), ema(close, 200)
    trend = 1.0 if (e20.iloc[-1] > e50.iloc[-1] > e200.iloc[-1]) else \
        (-1.0 if (e20.iloc[-1] < e50.iloc[-1] < e200.iloc[-1]) else 0.0)
    ma_dim = 1.0 if price > sma(close, 20).iloc[-1] else -1.0
    _, _, hist = macd(close)
    r = float(rsi(close).iloc[-1])
    momentum = np.clip((float(hist.iloc[-1]) > 0) * 0.5 + (r - 50) / 50 * 0.5, -1, 1)
    dims = {
        "趋势": _dim(trend),
        "均线": _dim(votes.get("ema", 0)),
        "动量": _dim(float(momentum)),
        "量能": _dim(votes.get("volume", 0)),
    }

    gauge = int(round((score + 1) / 2 * 100))   # 0-100，50 中性，对标参考站 49
    verdict = ("强烈看多" if score >= 0.6 else "偏多" if score >= 0.2
               else "强烈看空" if score <= -0.6 else "偏空" if score <= -0.2 else "中性")

    # 规则文字解读
    lines = []
    lines.append(f"现价 {price:.4f}，共振分 {score:+.2f}（{verdict}）。")
    lines.append(f"上方阻力 {res:.4f}，下方支撑 {sup:.4f}；参考止损 {stop:.4f}（2×ATR）。")
    lines.append("EMA 多头排列，趋势向上。" if trend > 0 else
                 "EMA 空头排列，趋势向下。" if trend < 0 else "EMA 纠缠，趋势不明。")
    lines.append(f"RSI {r:.0f}，" + ("偏强" if r > 55 else "偏弱" if r < 45 else "中性") +
                 "；MACD 柱" + ("翻红、动量向上。" if float(hist.iloc[-1]) > 0 else "翻绿、动量向下。"))
    lines.append("多指标共振偏多，可顺势关注做多。" if score >= 0.2 else
                 "多指标共振偏空，可顺势关注做空。" if score <= -0.2 else
                 "多空分歧、信号打架，建议观望等待方向明朗。")

    return {
        "price": round(price, 4), "resistance": round(res, 4),
        "support": round(sup, 4), "stop": stop,
        "gauge": gauge, "verdict": verdict, "score": round(score, 3),
        "dims": dims, "votes": votes,
        "atr": round(a, 4),
        "text": "".join(lines),
        "source": "规则引擎（技术指标），非大模型",
    }

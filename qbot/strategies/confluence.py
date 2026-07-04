"""多指标共振策略（Confluence）。

把多个技术指标合并成"投票打分"，多指标共同印证才开仓——这是职业做法，
而不是把 10 个指标全部 AND 死磕（那样几乎不开仓，且严重过拟合）。

投票成员（按角色分组，每类给一票，用上你清单里指标的集体智慧）：
  · EMA(20/50/200) 排列 —— 方向骨架
  · SuperTrend 方向     —— 工程化趋势（其线同时作移动止损）
  · MACD 柱             —— 动量
  · RSI                 —— 动量振荡（StochRSI 与其冗余，默认不重复计票）
  · 布林 %B             —— 价格在通道中的位置
  · OBV 斜率 + VWMA     —— 量价确认（真金白银在不在推）
总闸（不投票，只过滤）：ADX < adx_min 判定"没有趋势"→ 一律空仓，避免震荡市假信号。

得分 score ∈ [-1, +1]：各票按权重求和 / 总权重。
迟滞防抖：score ≥ enter_thr 才开多；score ≤ exit_thr 或 SuperTrend 翻空 才平。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy
from .indicators import adx, bollinger, ema, macd, obv, rsi, supertrend, vwma


class ConfluenceStrategy(Strategy):
    name = "confluence"

    def __init__(
        self,
        adx_min: float = 20.0,
        enter_thr: float = 0.5,
        exit_thr: float = 0.0,
        weights: dict | None = None,
        st_period: int = 10,
        st_mult: float = 3.0,
        allow_short: bool = False,
    ):
        super().__init__(adx_min=adx_min, enter_thr=enter_thr, exit_thr=exit_thr)
        self.adx_min = adx_min
        self.enter_thr = enter_thr
        self.exit_thr = exit_thr
        self.st_period = st_period
        self.st_mult = st_mult
        self.allow_short = allow_short
        # 允许做空 => 不再只做多；合约双向交易走这个
        self.long_only = not allow_short
        # 默认均衡权重；可自定义某类指标更重
        self.weights = weights or {
            "ema": 1.0, "supertrend": 1.0, "macd": 1.0,
            "rsi": 1.0, "bbands": 1.0, "volume": 1.0,
        }

    # ---------- 内部：算出每根K线的各指标票数 + 总分 ----------
    def _votes(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        e20, e50, e200 = ema(close, 20), ema(close, 50), ema(close, 200)
        _, st_dir = supertrend(df, self.st_period, self.st_mult)
        _, _, hist = macd(close)
        r = rsi(close, 14)
        _, _, _, pct_b, _ = bollinger(close, 20)
        ob = obv(close, df["volume"])
        vm = vwma(close, df["volume"], 20)
        adx_, _, _ = adx(df)

        v = pd.DataFrame(index=df.index)
        # EMA 排列：多头排列 +1，空头排列 -1，纠缠 0
        v["ema"] = np.select([(e20 > e50) & (e50 > e200), (e20 < e50) & (e50 < e200)],
                             [1.0, -1.0], default=0.0)
        # SuperTrend 方向
        v["supertrend"] = np.where(st_dir > 0, 1.0, -1.0)
        # MACD 柱正负
        v["macd"] = np.where(hist > 0, 1.0, np.where(hist < 0, -1.0, 0.0))
        # RSI 动量偏向（>55 多，<45 空，中间 0）
        v["rsi"] = np.select([r > 55, r < 45], [1.0, -1.0], default=0.0)
        # 布林 %B：站上中轨且未过度超买(<1) 视为多，跌破中轨视为空
        v["bbands"] = np.select([(pct_b > 0.5) & (pct_b < 1.0), pct_b < 0.5],
                                [1.0, -1.0], default=0.0)
        # 量价：OBV 上升 且 价在 VWMA 上方 → 多；反之空
        vol_up = (ob >= ob.shift(1)) & (close > vm)
        vol_dn = (ob < ob.shift(1)) & (close < vm)
        v["volume"] = np.select([vol_up, vol_dn], [1.0, -1.0], default=0.0)

        # 未算出指标（前若干根 NaN）的票置 0
        warmup = e200.isna() | adx_.isna() | vm.isna()
        v.loc[warmup] = 0.0

        # 加权总分 ∈ [-1,1]
        total_w = sum(self.weights.values())
        score = sum(v[k] * self.weights[k] for k in self.weights) / total_w
        v["score"] = score
        v["adx"] = adx_
        v["st_dir"] = st_dir.values
        # EMA200 大趋势过滤器（你的哲学：EMA 是所有信号的前提，不在下跌趋势里做多）
        v["trend_up"] = (close > e200).values
        v["warmup"] = warmup.values
        return v

    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        v = self._votes(df)
        score = v["score"].to_numpy()
        adx_ = v["adx"].to_numpy()
        st_dir = v["st_dir"].to_numpy()
        trend_up = v["trend_up"].to_numpy()
        warmup = v["warmup"].to_numpy()

        out = np.zeros(len(df))
        holding = 0.0
        for i in range(len(df)):
            if warmup[i]:
                out[i] = 0.0
                continue
            gate = (not np.isnan(adx_[i])) and adx_[i] >= self.adx_min
            if holding == 0.0:
                # 开多：站上EMA200(大趋势向上) + 有趋势 + 共振分够高
                if trend_up[i] and gate and score[i] >= self.enter_thr:
                    holding = 1.0
                # 开空（仅 allow_short）：跌破EMA200(下降趋势) + 有趋势 + 共振分够低
                elif self.allow_short and (not trend_up[i]) and gate \
                        and score[i] <= -self.enter_thr:
                    holding = -1.0
            elif holding > 0:
                # 平多：跌破EMA200 或 共振转弱 或 SuperTrend翻空（多指标发现行情转变）
                if (not trend_up[i]) or score[i] <= self.exit_thr or st_dir[i] < 0:
                    holding = 0.0
            else:  # holding < 0，持空
                # 平空（镜像）：上穿EMA200 或 共振转强 或 SuperTrend翻多
                if trend_up[i] or score[i] >= -self.exit_thr or st_dir[i] > 0:
                    holding = 0.0
            out[i] = holding
        return pd.Series(out, index=df.index)

    # ---------- 透明化：解释最新一根为什么给出这个信号 ----------
    def explain(self, df: pd.DataFrame) -> dict:
        v = self._votes(df)
        last = v.iloc[-1]
        votes = {k: int(last[k]) for k in self.weights}
        score = float(last["score"])
        conclusion = ("🟢 共振做多" if score >= self.enter_thr
                      else "🔴 共振偏空" if score <= -self.enter_thr
                      else "⚪ 分歧/观望")
        return {
            "score": round(score, 3),
            "adx": round(float(last["adx"]), 1) if not np.isnan(last["adx"]) else None,
            "votes": votes,
            "conclusion": conclusion,
        }

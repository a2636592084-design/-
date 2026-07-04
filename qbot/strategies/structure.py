"""高阶结构分析：道氏理论 / SMC(聪明钱) / 缠论（简化版）。

诚实边界（务必先读）：
- 结构点（摆动高/低点、分型）需要其**右侧若干根K线**才能确认，所以最右侧的结构
  是"待确认"的，会随新K线更新——这是所有结构分析的天然滞后，不是 bug。
- 缠论用**简化实现**：分型 → 笔 → 中枢，不含完整的线段递归与背驰段判定；
  SMC 用主流定义（BOS/CHoCH/订单块/FVG）；道氏用"高点抬高+低点抬高=上升趋势"。
- 这些都是**形态识别**，不是未来预测。用于辅助看清"现在处在什么结构里"，
  不构成任何收益保证。

只用当前及过去的数据（因果）。纯 numpy，无第三方依赖。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr


# ============================================================ 基础：摆动点
def _fractals(df: pd.DataFrame, k: int = 1) -> list[tuple[int, float, str]]:
    """分形摆动点：high[i] 为左右各 k 根里最高 → 摆动高('H')；对称为摆动低('L')。

    细粒度、点很多，专供缠论"分型"用。趋势结构请用 _pivots（带显著性过滤）。
    """
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(high)
    pts: list[tuple[int, float, str]] = []
    for i in range(k, n - k):
        seg_h = high[i - k:i + k + 1]
        seg_l = low[i - k:i + k + 1]
        if high[i] == seg_h.max() and int(seg_h.argmax()) == k:
            pts.append((i, float(high[i]), "H"))
        if low[i] == seg_l.min() and int(seg_l.argmin()) == k:
            pts.append((i, float(low[i]), "L"))
    return pts


def _pivots(df: pd.DataFrame, mult: float = 3.5) -> list[tuple[int, float, str]]:
    """显著摆动点（阈值 ZigZag）：只保留振幅 ≥ mult×典型ATR 的高低点，过滤小噪声。

    这是"高阶结构"的地基——用波动率自适应的阈值，让不同市场（美股/外汇/黄金/币）
    都只留下**真正的波段**，而不是每一根小回调都算一个结构点。严格因果：
    某个摆动点在价格反向走出阈值那一刻才被确认，最右侧的点是"待确认"的。
    """
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    if n < 3:
        return []
    # 逐根 ATR（Wilder EWM，天然因果：第 i 根只依赖 i 及之前）→ 阈值自适应且无未来函数。
    atr_arr = atr(df).to_numpy()
    fallback = float(df["close"].iloc[-1]) * 0.02
    piv: list[tuple[int, float, str]] = []
    direction = 0                      # 0/1=上行段(找高点), -1=下行段(找低点)
    up_i, up_p = 0, float(high[0])
    dn_i, dn_p = 0, float(low[0])
    for i in range(1, n):
        a = atr_arr[i]
        thr = (a if a == a and a > 0 else fallback) * mult
        if direction >= 0:
            if high[i] >= up_p:
                up_p, up_i = float(high[i]), i
            if up_p - low[i] >= thr:    # 自高点回落超阈值 → 确认一个摆动高
                piv.append((up_i, up_p, "H"))
                direction = -1
                dn_p, dn_i = float(low[i]), i
        else:
            if low[i] <= dn_p:
                dn_p, dn_i = float(low[i]), i
            if high[i] - dn_p >= thr:    # 自低点反弹超阈值 → 确认一个摆动低
                piv.append((dn_i, dn_p, "L"))
                direction = 1
                up_p, up_i = float(high[i]), i
    return piv


# ============================================================ 道氏理论
def dow_theory(zz: list[tuple[int, float, str]]) -> dict:
    """道氏：看最近两个摆动高、两个摆动低。
    高点抬高 + 低点抬高 = 上升趋势；高点降低 + 低点降低 = 下降趋势；否则震荡。
    """
    highs = [p for p in zz if p[2] == "H"]
    lows = [p for p in zz if p[2] == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return {"trend": "range", "score": 0.0, "desc": "结构点不足，趋势待确认。",
                "highs": [round(p[1], 4) for p in highs[-2:]],
                "lows": [round(p[1], 4) for p in lows[-2:]]}
    hh = highs[-1][1] > highs[-2][1]     # higher high
    hl = lows[-1][1] > lows[-2][1]       # higher low
    lh = highs[-1][1] < highs[-2][1]     # lower high
    ll = lows[-1][1] < lows[-2][1]       # lower low
    if hh and hl:
        trend, score, desc = "up", 1.0, "高点抬高、低点抬高 → 上升趋势（道氏多头）。"
    elif lh and ll:
        trend, score, desc = "down", -1.0, "高点降低、低点降低 → 下降趋势（道氏空头）。"
    elif hh and ll:
        trend, score, desc = "range", 0.0, "高点抬高但低点降低 → 扩张震荡，方向未定。"
    else:
        trend, score, desc = "range", 0.0, "高低点未同向 → 收敛/震荡，等待突破。"
    return {"trend": trend, "score": score, "desc": desc,
            "highs": [round(highs[-2][1], 4), round(highs[-1][1], 4)],
            "lows": [round(lows[-2][1], 4), round(lows[-1][1], 4)]}


# ============================================================ SMC 聪明钱
def smc_structure(df: pd.DataFrame, zz: list[tuple[int, float, str]]) -> dict:
    """SMC：结构破坏 BOS / 性质转变 CHoCH + 最近未回补 FVG + 订单块。"""
    events = []
    bias = 0            # +1 多头结构，-1 空头结构，0 未定
    last_high = None
    last_low = None
    for idx, price, kind in zz:
        if kind == "H":
            if last_high is not None and price > last_high:
                # 向上破前高
                kind_ev = "CHoCH" if bias < 0 else "BOS"
                events.append({"type": kind_ev, "dir": "up", "idx": idx,
                               "level": round(last_high, 4)})
                bias = 1
            last_high = price
        else:
            if last_low is not None and price < last_low:
                kind_ev = "CHoCH" if bias > 0 else "BOS"
                events.append({"type": kind_ev, "dir": "down", "idx": idx,
                               "level": round(last_low, 4)})
                bias = -1
            last_low = price

    # ---- FVG（公允价值缺口，3根K的价格失衡区）：只留最近未被回补的 ----
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(high)
    price_now = float(df["close"].iloc[-1])
    bull_fvg = None
    bear_fvg = None
    for i in range(2, n):
        if low[i] > high[i - 2]:                       # 向上跳空
            bottom, top = float(high[i - 2]), float(low[i])
            # 是否已被后续价格回补（价格重新覆盖缺口下沿）
            filled = (low[i + 1:] <= bottom).any() if i + 1 < n else False
            if not filled and bottom < price_now:      # 缺口在价下方 → 潜在支撑
                bull_fvg = {"bottom": round(bottom, 4), "top": round(top, 4)}
        elif high[i] < low[i - 2]:                     # 向下跳空
            bottom, top = float(high[i]), float(low[i - 2])
            filled = (high[i + 1:] >= top).any() if i + 1 < n else False
            if not filled and top > price_now:         # 缺口在价上方 → 潜在阻力
                bear_fvg = {"bottom": round(bottom, 4), "top": round(top, 4)}

    # ---- 订单块：最近一次结构破坏前的最后一根反向K线 ----
    order_block = None
    if events:
        ev = events[-1]
        j = ev["idx"]
        op = df["open"].to_numpy()
        cl = df["close"].to_numpy()
        if ev["dir"] == "up":                          # 找上涨前最后一根阴线
            for t in range(min(j, n - 1), max(j - 12, 0), -1):
                if cl[t] < op[t]:
                    order_block = {"dir": "bull", "low": round(float(low[t]), 4),
                                   "high": round(float(high[t]), 4)}
                    break
        else:                                          # 找下跌前最后一根阳线
            for t in range(min(j, n - 1), max(j - 12, 0), -1):
                if cl[t] > op[t]:
                    order_block = {"dir": "bear", "low": round(float(low[t]), 4),
                                   "high": round(float(high[t]), 4)}
                    break

    last_ev = events[-1] if events else None
    smc_score = 0.0
    if last_ev:
        smc_score = (1.0 if last_ev["dir"] == "up" else -1.0)
        if last_ev["type"] == "CHoCH":
            smc_score *= 0.6                            # 性质转变是反转信号，权重略降待确认
    return {"events": events[-4:], "last_event": last_ev, "score": smc_score,
            "bull_fvg": bull_fvg, "bear_fvg": bear_fvg, "order_block": order_block}


# ============================================================ 缠论（简化）
def chan_theory(df: pd.DataFrame) -> dict:
    """缠论简化版：分型 → 笔 → 中枢。

    - 分型：3根K里中间那根最高(顶分型)/最低(底分型)。
    - 笔：相邻顶底分型交替相连，且间隔≥4根K（近似缠论"独立一笔至少5K"）。
    - 中枢：连续3笔（4个转折点）价格区间的重叠 [ZD, ZG]，ZG>ZD 才成立。
    """
    fr = _fractals(df, k=1)                             # 3根K分型
    # 笔：交替 + 间隔约束
    bi: list[tuple[int, float, str]] = []
    for idx, price, kind in fr:
        if not bi:
            bi.append((idx, price, kind))
            continue
        if bi[-1][2] == kind:                          # 同类取更极端
            if (kind == "H" and price >= bi[-1][1]) or (kind == "L" and price <= bi[-1][1]):
                bi[-1] = (idx, price, kind)
        elif idx - bi[-1][0] >= 4:                     # 交替且间隔够 → 成一笔
            bi.append((idx, price, kind))

    # 中枢：最近一组3笔（4个转折点）的重叠区间
    center = None
    if len(bi) >= 4:
        last4 = bi[-4:]
        prices = [p[1] for p in last4]
        # 三段的高低：每相邻两点构成一段，取三段公共重叠
        seg_hi = [max(prices[i], prices[i + 1]) for i in range(3)]
        seg_lo = [min(prices[i], prices[i + 1]) for i in range(3)]
        zg = min(seg_hi)                               # 重叠上沿
        zd = max(seg_lo)                               # 重叠下沿
        if zg > zd:
            price_now = float(df["close"].iloc[-1])
            pos = ("上方" if price_now > zg else "下方" if price_now < zd else "内部")
            center = {"zd": round(zd, 4), "zg": round(zg, 4), "pos": pos}

    # 结构分：最后一笔方向（顶→底为下、底→顶为上）
    chan_score = 0.0
    direction = "未定"
    if len(bi) >= 2:
        last = bi[-1]
        if last[2] == "H":
            chan_score, direction = 1.0, "上涨笔（底→顶）"
        else:
            chan_score, direction = -1.0, "下跌笔（顶→底）"
    return {"fractals": len(fr), "bi": len(bi), "center": center,
            "direction": direction, "score": chan_score}


# ============================================================ 汇总
def structure_report(df: pd.DataFrame, mult: float = 3.5) -> dict:
    """三套高阶结构分析汇总 + 综合结构分（-1~1）+ 中文解读。"""
    if len(df) < 30:
        return {"error": "K线太少，无法做结构分析（建议≥60根）。"}
    zz = _pivots(df, mult=mult)
    dow = dow_theory(zz)
    smc = smc_structure(df, zz)
    chan = chan_theory(df)

    # 综合结构分：道氏(骨架) 0.5 + SMC 0.3 + 缠论 0.2
    score = 0.5 * dow["score"] + 0.3 * smc["score"] + 0.2 * chan["score"]
    score = float(np.clip(score, -1, 1))
    gauge = int(round((score + 1) / 2 * 100))
    verdict = ("结构强多" if score >= 0.5 else "结构偏多" if score >= 0.15
               else "结构强空" if score <= -0.5 else "结构偏空" if score <= -0.15
               else "结构中性")

    # 中文解读
    lines = [dow["desc"]]
    ev = smc["last_event"]
    if ev:
        w = "向上" if ev["dir"] == "up" else "向下"
        nm = "结构破坏(BOS)延续" if ev["type"] == "BOS" else "性质转变(CHoCH)，警惕反转"
        lines.append(f"SMC：最近{w}{nm}，关键位 {ev['level']}。")
    else:
        lines.append("SMC：暂无明确结构破坏。")
    if smc["order_block"]:
        ob = smc["order_block"]
        lines.append(f"订单块 {ob['low']}~{ob['high']}（{'需求区' if ob['dir']=='bull' else '供给区'}），回踩可关注。")
    if smc["bull_fvg"]:
        lines.append(f"下方未回补缺口(FVG) {smc['bull_fvg']['bottom']}~{smc['bull_fvg']['top']}，潜在支撑。")
    if smc["bear_fvg"]:
        lines.append(f"上方未回补缺口(FVG) {smc['bear_fvg']['bottom']}~{smc['bear_fvg']['top']}，潜在阻力。")
    if chan["center"]:
        c = chan["center"]
        lines.append(f"缠论中枢 {c['zd']}~{c['zg']}，现价在中枢{c['pos']}（{chan['direction']}）。")
    else:
        lines.append(f"缠论：{chan['bi']} 笔，暂未形成有效中枢（{chan['direction']}）。")

    return {
        "gauge": gauge, "score": round(score, 3), "verdict": verdict,
        "dow": dow, "smc": smc, "chan": chan,
        "swings": [{"idx": p[0], "price": round(p[1], 4), "kind": p[2]} for p in zz[-8:]],
        "text": "".join(lines),
        "note": "结构分析为形态识别，最右侧结构待新K线确认，非未来预测。",
    }

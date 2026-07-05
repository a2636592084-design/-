"""组合回测：把"实盘那一整套"拿历史数据快速跑一遍。

和单标的回测(backtest/engine.py)不同，这里复现的是 PortfolioEngine 的**全套规则**：
全市场扫描 → 按共振分排序择优 → 最多持仓 N → ATR/固定止损 + 保本上移 + 移动止损 +
被止损冷却 + 按波动定仓位 + 单日/组合熔断。让你在上模拟盘前，几秒就知道这套配置
过去一年是赚是亏、回撤多大。

诚实/防未来函数：
- 信号、共振分、ATR 全部 **shift(1)**（用上一根已收盘的信息决定这一根怎么做），不偷看未来。
- 止损/止盈按当根的最高/最低价判定是否被触及（盘中触发），成交价取触发价（保守）。
- 计手续费 + 滑点。空头用"现金+持仓市值"统一记账，MTM 正确。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.loader import get_ohlcv
from ..logger import get_logger
from ..strategies.indicators import atr as _atr
from .metrics import compute_metrics

log = get_logger("qbot.backtest.portfolio")

_PPY = {"1m": 525600, "5m": 105120, "15m": 35040, "30m": 17520,
        "1h": 8760, "2h": 4380, "4h": 2190, "6h": 1460, "12h": 730,
        "1d": 365, "1w": 52, "1M": 12}


def _prep(market, universe, strategy_factory, timeframe, limit, allow_short):
    """逐标的拉数据并预计算(信号/共振分/ATR，均 shift(1) 防未来)。并发拉取以提速。"""
    from concurrent.futures import ThreadPoolExecutor

    def load(sym):
        try:
            df = get_ohlcv(market, sym, timeframe, limit=limit, fallback_synthetic=False)
        except Exception as e:  # noqa: BLE001
            log.debug("回测拉取 %s 失败: %s", sym, e)
            return None
        if df is None or len(df) < 60:
            return None
        strat = strategy_factory()
        pos = strat.generate_positions(df).fillna(0.0)
        if getattr(strat, "long_only", True):
            pos = pos.clip(lower=0.0)
        score = strat._votes(df)["score"] if hasattr(strat, "_votes") \
            else df["close"].pct_change(20)
        a = _atr(df)
        return sym, pd.DataFrame({
            "close": df["close"], "high": df["high"], "low": df["low"],
            "sig": pos.shift(1), "score": score.shift(1).abs(), "atr": a.shift(1),
        })

    data = {}
    with ThreadPoolExecutor(max_workers=6) as ex:   # 并发拉取(网络I/O释放GIL)，大幅提速
        for res in ex.map(load, universe):
            if res:
                data[res[0]] = res[1]
    return data


def backtest_portfolio(
    market: str, universe: list[str], strategy_factory, timeframe: str = "4h",
    limit: int = 1200, max_positions: int = 4, capital: float = 100_000.0,
    stop_loss: float = 0.0, take_profit: float = 0.0, breakeven: float = 0.0,
    trailing_stop: float = 0.0, atr_stop_mult: float = 2.5, cooldown: int = 3,
    fee: float = 0.0005, slippage: float = 0.0005, risk_per_trade: float = 0.01,
    max_pos_per_symbol: float = 0.20, max_daily_loss: float = 0.10,
    max_drawdown: float = 0.20,
    market_gate: bool = False, gate_adx: float = 20.0, gate_ema: int = 200,
) -> dict:
    data = _prep(market, universe, strategy_factory, timeframe, limit, atr_stop_mult)
    if not data:
        return {"error": "没有可回测的标的（网络/代理？或历史太短）。"}

    idx = sorted(set().union(*[d.index for d in data.values()]))
    # 对齐到统一时间轴；价格/信号/分数/ATR 前向填充（因果），上市前留 NaN=不可用
    for sym in data:
        data[sym] = data[sym].reindex(idx).ffill()

    # 大盘方向闸：用 BTC 算逐根大盘态并 shift(1)（不偷看未来）。震荡→不开、多头只做多、空头只做空。
    regime = None
    if market_gate:
        btc = next((s for s in data if str(s).upper().split("/")[0] == "BTC"), None)
        if btc:
            from ..strategies.regime import regime_series
            regime = regime_series(data[btc], adx_min=gate_adx, ema_len=gate_ema).shift(1)
        else:
            log.warning("开了大盘闸但 universe 里没有 BTC，无法判定大盘态，本次不拦截。")

    cash = capital
    pos: dict[str, dict] = {}          # sym -> {amt, avg, stop, peak}
    cooldown_until: dict[str, int] = {}
    closed: list[float] = []
    eq_ts, eq_val = [], []
    peak_eq = capital
    halted = False
    day = None
    day_start_eq = capital
    daily_halted = False

    def price(sym, t):
        v = data[sym].at[t, "close"]
        return float(v) if v == v else None

    def do_close(sym, fill, tick):
        nonlocal cash
        p = pos.pop(sym)
        qty = abs(p["amt"])
        notional = fill * qty
        realized = (qty * (fill - p["avg"])) if p["amt"] > 0 else (qty * (p["avg"] - fill))
        realized -= notional * fee
        cash += (notional if p["amt"] > 0 else -notional)   # 平多收钱、平空付钱
        closed.append(realized)
        if cooldown:
            cooldown_until[sym] = tick + cooldown

    for tick, t in enumerate(idx):
        # 当日起点（跨日复位单日熔断）
        d = t.date() if hasattr(t, "date") else None
        if d != day:
            day, day_start_eq, daily_halted = d, None, False

        # 盯市权益
        equity = cash + sum(p["amt"] * (price(s, t) or p["avg"]) for s, p in pos.items())
        if day_start_eq is None:
            day_start_eq = equity
        peak_eq = max(peak_eq, equity)
        if peak_eq > 0 and equity / peak_eq - 1 <= -max_drawdown:
            halted = True
        if day_start_eq > 0 and equity / day_start_eq - 1 <= -max_daily_loss:
            daily_halted = True
        eq_ts.append(str(t)); eq_val.append(equity)

        # 组合熔断：全平
        if halted:
            for s in list(pos):
                px = price(s, t)
                if px:
                    do_close(s, px * (1 - slippage if pos[s]["amt"] > 0 else 1 + slippage), tick)
            continue

        # 1) 价格止损/止盈/移动止损（盘中触发）
        for s in list(pos):
            row = data[s].loc[t]
            hi, lo, cl = float(row["high"]), float(row["low"]), float(row["close"])
            if cl != cl:
                continue
            p = pos[s]; entry = p["avg"]; is_long = p["amt"] > 0
            av = float(row["atr"]) if row["atr"] == row["atr"] else None
            dist = (atr_stop_mult * av) if (atr_stop_mult and av) else (stop_loss * entry if stop_loss else None)
            p["peak"] = max(p["peak"], hi) if is_long else min(p["peak"], lo)
            stop = p["stop"]
            if breakeven:
                if (hi >= entry * (1 + breakeven)) if is_long else (lo <= entry * (1 - breakeven)):
                    stop = max(stop, entry) if is_long else min(stop, entry)
            if dist:
                trail = (p["peak"] - dist) if is_long else (p["peak"] + dist)
                stop = max(stop, trail) if is_long else min(stop, trail)
            p["stop"] = stop
            hit = (lo <= stop) if is_long else (hi >= stop)
            if (stop_loss or trailing_stop or breakeven or atr_stop_mult) and hit:
                do_close(s, stop * (1 - slippage if is_long else 1 + slippage), tick)
                continue
            if take_profit:
                tp = entry * (1 + take_profit) if is_long else entry * (1 - take_profit)
                if (hi >= tp) if is_long else (lo <= tp):
                    do_close(s, tp * (1 - slippage if is_long else 1 + slippage), tick)

        # 2) 信号变化平仓
        for s in list(pos):
            sig = data[s].at[t, "sig"]
            cur_side = 1 if pos[s]["amt"] > 0 else -1
            want = 1 if sig > 0.5 else (-1 if sig < -0.5 else 0)
            if want != cur_side:
                px = price(s, t)
                if px:
                    do_close(s, px * (1 - slippage if pos[s]["amt"] > 0 else 1 + slippage), tick)

        if daily_halted:
            continue

        # 大盘方向闸：BTC震荡→本轮不开新仓；多头只开多、空头只开空
        reg = None
        if regime is not None:
            rv = regime.at[t]
            reg = int(rv) if rv == rv else None      # NaN(预热期)→不限制
        if reg == 0:
            continue

        # 3) 开仓：按|共振分|排序择优，填满剩余仓位
        cands = []
        for s, dd in data.items():
            if s in pos:
                continue
            if tick < cooldown_until.get(s, 0):
                continue
            sig = dd.at[t, "sig"]
            if abs(sig) <= 0.5:
                continue
            side_i = 1 if sig > 0 else -1
            if reg is not None and reg != 0 and side_i != reg:   # 与大盘方向不符→跳过
                continue
            sc = dd.at[t, "score"]
            cands.append((abs(sc) if sc == sc else 0.0, s, side_i))
        cands.sort(reverse=True)
        weight = min(1.0 / max_positions, max_pos_per_symbol)
        for _, s, side in cands:
            if len(pos) >= max_positions:
                break
            px = price(s, t)
            if not px or px <= 0:
                continue
            is_long = side > 0
            av = float(data[s].at[t, "atr"]) if data[s].at[t, "atr"] == data[s].at[t, "atr"] else None
            if atr_stop_mult and av:                       # 按波动定仓位
                stop_dist = atr_stop_mult * av
                units = (equity * risk_per_trade) / stop_dist
                units = min(units, equity * max_pos_per_symbol / px)
            else:
                units = equity * weight / px
            if units <= 0:
                continue
            fill = px * (1 + slippage if is_long else 1 - slippage)
            notional = fill * units
            cash -= (notional if is_long else -notional)   # 开多付钱、开空收钱
            cash -= notional * fee
            init_stop = ((fill - (atr_stop_mult * av if (atr_stop_mult and av) else stop_loss * fill))
                         if is_long else
                         (fill + (atr_stop_mult * av if (atr_stop_mult and av) else stop_loss * fill))) \
                if (atr_stop_mult and av) or stop_loss else (0.0 if is_long else 1e18)
            pos[s] = {"amt": units if is_long else -units, "avg": fill,
                      "stop": init_stop, "peak": fill}

    equity = pd.Series(eq_val, index=pd.to_datetime(eq_ts))
    ppy = _PPY.get(timeframe, 365)
    m = compute_metrics(equity, periods_per_year=ppy)
    pnls = np.array(closed)
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    gl = abs(losses.sum())
    m.update({
        "num_trades": int(len(pnls)),
        "win_rate": float(len(wins) / len(pnls)) if len(pnls) else 0.0,
        "profit_factor": float(wins.sum() / gl) if gl > 0 else None,
        "final_equity": float(equity.iloc[-1]) if len(equity) else capital,
    })
    # 资金曲线降采样给前端画图（始终包含最后一个点，与 final_equity 一致）
    step = max(1, len(equity) // 300)
    pts = list(equity.items())
    sampled = pts[::step]
    if not sampled or sampled[-1][0] != pts[-1][0]:
        sampled.append(pts[-1])
    curve = [{"ts": str(ts), "equity": round(float(v), 2)} for ts, v in sampled]
    return {"metrics": m, "equity_curve": curve, "symbols": len(data),
            "bars": len(idx), "timeframe": timeframe,
            "note": "组合回测：复现实盘全套规则，信号已shift防未来函数，计手续费+滑点。"}

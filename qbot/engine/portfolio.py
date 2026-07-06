"""全市场组合引擎：扫描全部标的 → 按信号/共振分排序 → 择优持有有限个仓位。

这是"全市场交易"的正确姿势：检测可以覆盖全部，持仓必须有限（受 max_positions、
单标的上限、组合回撤熔断约束）。绝不"把所有标的都买一遍"（资金/风控都不允许）。

execute=False 时只出信号不下单（A股实盘=信号+手动 就走这个）。
状态每轮落盘 logs/portfolio_<mode>.json，供面板三个交易页读取。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..broker.base import Broker, Order
from ..data.loader import get_ohlcv
from ..journal import TradeJournal
from ..logger import get_logger
from ..risk.manager import RiskManager
from ..strategies.base import Strategy

log = get_logger("qbot.portfolio")
_STATE_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


@dataclass
class PortfolioState:
    running: bool = False
    ticks: int = 0
    equity: float = 0.0
    recent_trades: list = field(default_factory=list)


class PortfolioEngine:
    def __init__(
        self,
        mode: str,               # 展示用标签：paper / ashare / crypto
        market: str,             # 数据市场：crypto / ashare / synthetic
        universe: list[str],
        strategy: Strategy,
        broker: Broker,
        risk: RiskManager,
        max_positions: int = 12,
        timeframe: str = "1d",
        lookback: int = 300,
        poll_seconds: int = 300,
        demo: bool = True,
        execute: bool = True,    # False=只出信号不下单（A股实盘）
        notify: bool = False,
        stop_loss: float = 0.08,       # 初始硬止损：开仓即在成本±此比例设保护线(0=关)
        take_profit: float = 0.25,     # 硬止盈：单仓盈利达此比例强制平仓(0=关)
        trailing_stop: float = 0.0,    # 移动止损：止损线跟随峰值、留此比例回撤空间(0=关)
        breakeven_trigger: float = 0.0,  # 保本上移：盈利达此比例即把止损抬到成本(0=关)
        atr_stop_mult: float = 0.0,    # ATR自适应止损：止损距离=此倍数×ATR(>0则覆盖固定%，并按波动定仓位)
        cooldown: int = 0,             # 冷却：某标的被止损后，隔此多少个扫描周期才允许再进(0=关)
        exchange_stops: bool = True,   # 合约：开仓同步在OKX挂真实止损条件单(程序停了也有保护)
        market_gate: bool = False,     # 大盘方向闸：只在BTC自己有趋势时才开新仓(震荡休息)
        gate_adx: float = 20.0,        # 大盘闸的ADX门槛(BTC ADX<此值=震荡→暂停开新仓)
        gate_ema: int = 200,           # 大盘闸的均线长度(BTC收盘在EMA上=多头行情、下=空头行情)
    ):
        self.mode = mode
        self.market = market
        self.universe = universe
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self.max_positions = max_positions
        self.timeframe = timeframe
        self.lookback = lookback
        self.poll_seconds = poll_seconds
        self.demo = demo
        self.execute = execute
        self.notify = notify
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.trailing_stop = trailing_stop
        self.breakeven_trigger = breakeven_trigger
        self.atr_stop_mult = atr_stop_mult
        self.cooldown = cooldown
        self.exchange_stops = exchange_stops
        self.market_gate = market_gate
        self.gate_adx = gate_adx
        self.gate_ema = gate_ema
        # 大盘基准标的：从 universe 里挑 BTC（自动匹配现货/合约的symbol格式），没有则退回 BTC/USDT
        self._bench_symbol = next(
            (s for s in universe if str(s).upper().split("/")[0] == "BTC"), "BTC/USDT")
        self._regime: int | None = None       # 本轮大盘态：+1多头/-1空头/0震荡/None=闸关或不适用
        self.state = PortfolioState()
        self._held_prev: set[str] = set()
        self._notifiers = None
        self._untradeable: set[str] = set()   # 记住下不了单的标的(如模拟盘不支持)，不再重试
        self.journal = TradeJournal(mode)     # 交易日志 + 真实资金曲线 + 实盘统计
        self._funding: dict = {}              # 合约资金费率缓存 {symbol: {...}}
        self._stop_price: dict = {}           # 每仓当前保护止损价(只升不降、锁盈)
        self._peak_price: dict = {}           # 每仓自开仓以来的有利方向极值价
        self._liq: dict = {}                  # 合约强平价缓存 {symbol: price}
        self._atr: dict = {}                  # 每标的最新 ATR(绝对值)，供ATR止损/波动定仓位
        self._cooldown_until: dict = {}       # {symbol: 到第几个tick前不再进}
        self._exch_stops: dict = {}           # 交易所真实止损单 {symbol: {id, level, side}}

    # ---------- 扫描全市场，返回每个标的的信号+分数+价格 ----------
    def _scan(self) -> list[dict]:
        rows = []
        for sym in self.universe:
            try:
                df = get_ohlcv(self.market, sym, self.timeframe,
                               limit=self.lookback, demo=self.demo,
                               fallback_synthetic=(self.market == "synthetic"))
                pos = self.strategy.generate_positions(df).fillna(0.0)
                if self.strategy.long_only:
                    pos = pos.clip(lower=0.0)
                target = float(pos.iloc[-1]) if len(pos) else 0.0
                price = float(df["close"].iloc[-1])
                # 记录 ATR（波动率）：ATR止损与按波动定仓位都要用
                try:
                    from ..strategies.indicators import atr as _atr_ind
                    av = float(_atr_ind(df).iloc[-1])
                    if av == av and av > 0:
                        self._atr[sym] = av
                except Exception:  # noqa: BLE001
                    pass
                # 排序强度：共振策略用共振分，否则用20根动量
                if hasattr(self.strategy, "explain"):
                    strength = float(self.strategy.explain(df).get("score") or 0.0)
                else:
                    strength = float(df["close"].iloc[-1] / df["close"].iloc[-min(21, len(df))] - 1.0)
                rows.append({"symbol": sym, "price": price, "target": target,
                             "long": target > 0.5, "short": target < -0.5,
                             "strength": round(strength, 4)})
            except Exception as e:  # noqa: BLE001
                log.debug("扫描 %s 失败: %s", sym, e)
        return rows

    def _market_regime(self) -> int | None:
        """本轮大盘态：+1多头(只做多)/-1空头(只做空)/0震荡(暂停开新仓)。
        闸关闭、或非加密市场(BTC基准不适用) → None，不做任何限制。"""
        if not self.market_gate or self.market != "crypto":
            return None
        try:
            from ..strategies.regime import regime_now
            df = get_ohlcv(self.market, self._bench_symbol, self.timeframe,
                           limit=self.lookback, demo=self.demo,
                           fallback_synthetic=(self.market == "synthetic"))
            return regime_now(df, adx_min=self.gate_adx, ema_len=self.gate_ema)
        except Exception as e:  # noqa: BLE001
            log.debug("大盘态判定失败(%s)，本轮不拦截开仓。", e)
            return None

    def tick(self) -> dict:
        self._regime = self._market_regime()
        scan = self._scan()
        if not scan:
            log.warning("本轮无有效标的（网络？），跳过。")
            return self._write_state(scan, [])

        prices = {r["symbol"]: r["price"] for r in scan}
        if hasattr(self.broker, "set_price"):
            for s, p in prices.items():
                self.broker.set_price(s, p)

        account = self.broker.get_account()
        equity = account.equity(prices)
        self.risk.update_equity(equity)
        self.journal.record_equity(equity)        # 真实资金曲线
        held = {s for s, p in account.positions.items() if abs(p.amount) > 1e-12}

        # 合约：读强平价 + 资金费率（供面板预警）。失败不影响主流程。
        self._liq = {s: p.liquidation_price for s, p in account.positions.items()
                     if getattr(p, "liquidation_price", 0.0)}
        if held and hasattr(self.broker, "fetch_funding"):
            try:
                self._funding = self.broker.fetch_funding(list(held))
            except Exception as e:  # noqa: BLE001
                log.debug("资金费率拉取失败: %s", e)

        trades = []
        if self.execute:
            trades = self._rebalance(scan, prices, account, equity, held)
            # 收尾：把交易所真实止损单与当前持仓/软件止损位同步（新仓补挂、上移改单、残单撤销）
            try:
                self._sync_exch_stops(self.broker.get_account())
            except Exception as e:  # noqa: BLE001
                log.debug("同步交易所止损单失败: %s", e)
        else:
            # 只出信号不下单（A股实盘=手动）：用扫描结果推导"建议持有"清单
            pass

        self.state.ticks += 1
        self.state.equity = equity
        self._maybe_notify(scan, held)
        return self._write_state(scan, trades)

    def _stop_dist(self, sym: str, entry: float) -> float | None:
        """止损距离(绝对价格)：ATR模式=倍数×ATR(波动自适应)；否则=固定%×成本。"""
        if self.atr_stop_mult and self._atr.get(sym):
            return self.atr_stop_mult * self._atr[sym]
        return self.stop_loss * entry if self.stop_loss else None

    def stop_level(self, sym: str, entry: float, is_long: bool) -> float | None:
        """某仓当前的保护止损价（已把保本/移动的上移算进去）。供面板显示。"""
        if sym in self._stop_price:
            return self._stop_price[sym]
        dist = self._stop_dist(sym, entry)
        if not dist:
            return None
        return entry - dist if is_long else entry + dist

    def _apply_stops(self, prices, account, held, trades) -> None:
        """价格止损/止盈/移动止损：独立于信号、每轮最高优先级。

        止损线【只升不降】随利润上移(锁盈)：
          · 初始：成本 ∓ stop_loss（多单在下方、空单在上方）。
          · 保本上移：盈利达 breakeven_trigger → 止损抬到成本（此后不亏）。
          · 移动止损：止损跟随峰值、留 trailing_stop 回撤空间（利润越走越锁）。
        这是"信号还没反转但价格已经打脸/或已到手利润"时的硬保护。"""
        for s in list(self._stop_price):      # 清理已平仓标的的记录
            if s not in held:
                self._stop_price.pop(s, None)
                self._peak_price.pop(s, None)
        for sym in list(held):
            p = account.positions[sym]
            entry, px = p.avg_price, prices.get(sym)
            if not entry or not px or entry <= 0:
                continue
            is_long = p.amount > 0
            dist = self._stop_dist(sym, entry)         # 止损距离(ATR或固定%)
            atr_mode = bool(self.atr_stop_mult and self._atr.get(sym))
            # 初始化：保护止损 + 峰值
            if sym not in self._stop_price:
                if dist:
                    self._stop_price[sym] = entry - dist if is_long else entry + dist
                else:
                    self._stop_price[sym] = 0.0 if is_long else float("inf")
                self._peak_price[sym] = px
            peak = self._peak_price[sym] = (max(self._peak_price[sym], px) if is_long
                                            else min(self._peak_price[sym], px))
            stop = self._stop_price[sym]
            raise_ = (lambda a, b: max(a, b)) if is_long else (lambda a, b: min(a, b))
            # 保本上移：盈利达触发比例 → 止损抬到成本
            if self.breakeven_trigger:
                trig = entry * (1 + self.breakeven_trigger) if is_long \
                    else entry * (1 - self.breakeven_trigger)
                if (px >= trig) if is_long else (px <= trig):
                    stop = raise_(stop, entry)
            # 移动止损：ATR模式=峰值∓N×ATR(吊灯止损)；否则=峰值×(1∓trailing%)
            if atr_mode and dist:
                trail = peak - dist if is_long else peak + dist
                stop = raise_(stop, trail)
            elif self.trailing_stop:
                trail = peak * (1 - self.trailing_stop) if is_long \
                    else peak * (1 + self.trailing_stop)
                stop = raise_(stop, trail)
            self._stop_price[sym] = stop

            # 判定平仓
            init = (entry - dist if is_long else entry + dist) if dist \
                else (0.0 if is_long else float("inf"))
            locked = (stop >= entry) if is_long else (stop <= entry)          # 已保本/锁盈
            moved = (stop > init + 1e-9) if is_long else (stop < init - 1e-9)  # 止损线上移过
            hit_stop = (px <= stop) if is_long else (px >= stop)
            base_label = "ATR止损" if atr_mode else f"止损{self.stop_loss*100:.0f}%"
            reason = None
            if (self.stop_loss or self.trailing_stop or self.breakeven_trigger or self.atr_stop_mult) and hit_stop:
                reason = ("移动止损(锁盈)" if locked else "移动止损" if moved else base_label)
            elif self.take_profit:
                tp = entry * (1 + self.take_profit) if is_long else entry * (1 - self.take_profit)
                if (px >= tp) if is_long else (px <= tp):
                    reason = f"止盈{self.take_profit*100:.0f}%"
            if reason:
                pnl = (px / entry - 1) if is_long else (entry / px - 1)
                log.info("[%s] %s 触发%s（浮盈%.2f%% · 止损线%.4f）→ 平仓",
                         self.mode, sym, reason, pnl * 100, stop)
                if self._close(sym, account, prices, trades, reason=reason):
                    held.discard(sym)
                    self._stop_price.pop(sym, None)
                    self._peak_price.pop(sym, None)
                    if self.cooldown:                  # 被止损后进入冷却，防止立刻追回震荡
                        self._cooldown_until[sym] = self.state.ticks + self.cooldown

    def _rebalance(self, scan, prices, account, equity, held) -> list[dict]:
        """双向组合再平衡：多空皆可，多指标发现方向反转即平仓。"""
        trades = []
        # 价格止损/止盈/移动止损：最高优先级，先于信号与开仓（熔断除外）
        self._apply_stops(prices, account, held, trades)
        # 组合回撤熔断：全部平仓、停开新仓
        if self.risk.halted:
            for sym in list(held):
                self._close(sym, account, prices, trades, reason="组合熔断")
            log.warning("组合回撤熔断已触发：全部平仓、停开新仓。")
            return trades

        # 每个标的的目标方向：+1 多 / -1 空 / 0 空仓
        desired = {r["symbol"]: (1 if r["target"] > 0.5 else (-1 if r["target"] < -0.5 else 0))
                   for r in scan}

        # 1) 平仓：持仓方向与最新信号不一致（含转为观望、或反向）
        for sym in list(held):
            cur = account.positions[sym].amount
            cur_side = 1 if cur > 0 else -1
            if desired.get(sym, 0) != cur_side:
                if self._close(sym, account, prices, trades, reason="信号变化"):
                    held.discard(sym)

        # 单日亏损熔断：允许平仓(上一步已做)，但今日不再开新仓（次日复位）
        if self.risk.daily_halted:
            log.warning("单日亏损熔断：今日停开新仓，已有持仓保留（明日自动复位）。")
            return trades

        # 大盘方向闸：BTC震荡→本轮不开新仓；BTC多头→只开多；BTC空头→只开空（已有仓不受影响）
        reg = self._regime
        if reg == 0:
            log.info("[%s] 大盘震荡(BTC ADX<%.0f)：本轮暂停开新仓，已有持仓的止损/信号平仓照常。",
                     self.mode, self.gate_adx)
            return trades

        # 2) 开仓：按共振分绝对值排序，填满剩余仓位（多头买入开、空头卖出开）
        weight = min(1.0 / self.max_positions, self.risk.cfg.max_position_per_symbol)
        candidates = sorted(
            (r for r in scan if abs(r["target"]) > 0.5 and r["symbol"] not in held
             and r["symbol"] not in self._untradeable
             and self.state.ticks >= self._cooldown_until.get(r["symbol"], 0)  # 冷却中跳过
             # 大盘闸：多头行情只留做多信号、空头行情只留做空信号
             and (reg is None or (reg > 0 and r["target"] > 0) or (reg < 0 and r["target"] < 0))),
            key=lambda r: abs(r["strength"]), reverse=True)
        for r in candidates:
            if len(held) >= self.max_positions:
                break
            sym, price = r["symbol"], r["price"]
            amt = self._size(sym, price, equity, weight, r["target"] > 0)
            if amt <= 0:
                continue
            is_long = r["target"] > 0
            side = "buy" if is_long else "sell"           # 卖出=开空
            if self._safe_submit(sym, side, amt, price, trades):
                held.add(sym)
                # 预置软件止损并立刻在交易所挂真实止损单（避免新仓裸奔）
                dist = self._stop_dist(sym, price)
                if dist:
                    stop0 = price - dist if is_long else price + dist
                    self._stop_price[sym] = stop0
                    self._peak_price[sym] = price
                    self._place_exch_stop(sym, is_long, amt, stop0)
        return trades

    def _size(self, sym, price, equity, weight, is_long) -> float:
        """仓位数量。ATR模式=按波动定仓位（每单风险≈风险预算1%，波动大则仓位小，
        风险均衡）；否则=等权（总资金/最多持仓）。"""
        if price <= 0:
            return 0.0
        if self.atr_stop_mult and self._atr.get(sym):
            stop_dist = self.atr_stop_mult * self._atr[sym]
            stop_price = price - stop_dist if is_long else price + stop_dist
            return self.risk.position_size_by_risk(price, stop_price, equity)
        return (equity * weight) / price

    # ---------- 交易所级真实止损单（程序停了也有保护） ----------
    def _stops_supported(self) -> bool:
        return bool(self.exchange_stops and hasattr(self.broker, "place_stop")
                    and getattr(self.broker, "trade_type", "spot") == "swap")

    def _place_exch_stop(self, sym, is_long, amt, stop) -> None:
        """在交易所挂/改真实止损单：先撤旧单再挂新单。失败不影响交易(软件止损兜底)。"""
        if not self._stops_supported() or not stop or amt <= 0:
            return
        close_side = "sell" if is_long else "buy"     # 平多=卖、平空=买
        try:
            old = self._exch_stops.get(sym)
            if old and old.get("id"):
                self.broker.cancel_stop(sym, old["id"])
            aid = self.broker.place_stop(sym, close_side, abs(amt), stop)
            if aid:
                self._exch_stops[sym] = {"id": aid, "level": float(stop), "side": close_side}
                log.info("[%s] %s 已在OKX挂真实止损单 @ %.6f (id=%s)", self.mode, sym, stop, aid)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] %s 交易所止损单操作失败: %s（软件止损仍生效）",
                        self.mode, sym, str(e)[:90])

    def _sync_exch_stops(self, account) -> None:
        """每轮收尾：给新仓补挂止损、把已上移的止损同步改单、撤掉已平仓的残单。"""
        if not self._stops_supported():
            return
        held = {s for s, p in account.positions.items() if abs(p.amount) > 1e-12}
        for s in list(self._exch_stops):              # 已不持有 → 撤残单
            if s not in held:
                try:
                    self.broker.cancel_stop(s, self._exch_stops[s].get("id"))
                except Exception:  # noqa: BLE001
                    pass
                self._exch_stops.pop(s, None)
        for s in held:
            stop = self._stop_price.get(s)
            if not stop or stop in (0.0, float("inf")):
                continue
            p = account.positions[s]
            cur = self._exch_stops.get(s)
            # 只在无单 或 止损位移动>0.2% 时改单，避免频繁撤挂
            if (not cur) or abs(stop - cur["level"]) / max(abs(stop), 1e-9) > 0.002:
                self._place_exch_stop(s, p.amount > 0, abs(p.amount), stop)

    def _close(self, sym, account, prices, trades, reason="信号") -> bool:
        """平掉某标的的现有仓位（多单卖出平、空单买入平）。"""
        cur = account.positions[sym].amount
        px = prices.get(sym, account.positions[sym].avg_price)
        if cur > 0:
            return self._safe_submit(sym, "sell", cur, px, trades, reason=reason)
        if cur < 0:
            return self._safe_submit(sym, "buy", -cur, px, trades, reason=reason)
        return True

    def _safe_submit(self, sym, side, amt, price, trades, reason="") -> bool:
        """下单，失败只跳过该标的、不中断整轮。返回是否成功。"""
        try:
            fill = self.broker.submit(Order(sym, side, amt))
            px = float(getattr(fill, "price", 0.0)) or price
            fee = float(getattr(fill, "fee", 0.0) or 0.0)
            filled = float(getattr(fill, "amount", 0.0)) or amt
            self.journal.record_fill(side, sym, filled, px, fee)   # 记入交易日志
            trades.append(self._trade(side, sym, filled, px, reason))
            return True
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "posSide" in msg or "51000" in msg:
                if not getattr(self, "_warned_posmode", False):
                    log.error("下单被拒(posSide/51000)：OKX 账户是【双向持仓】模式，本系统按【单向】"
                              "交易。请在 OKX 交易设置里改成【单向持仓/买卖模式】，再重启。")
                    self._warned_posmode = True
            elif "does not have market symbol" in msg or "BadSymbol" in msg or "51001" in msg:
                self._untradeable.add(sym)   # 该标的（模拟盘）不可交易，记下不再重试
                log.warning("%s 不可交易（%s模拟盘可能不支持），已跳过。", sym, "OKX")
            else:
                log.warning("%s 下单失败：%s（跳过）", sym, msg[:100])
            return False

    def _trade(self, side, sym, amt, price, reason="") -> dict:
        rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "side": side, "symbol": sym, "amount": round(amt, 6),
               "price": round(price, 6), "reason": reason}
        self.state.recent_trades.append(rec)
        self.state.recent_trades = self.state.recent_trades[-40:]
        log.info("[%s] %s %s x%.6f @ %.6f", self.mode, side, sym, amt, price)
        return rec

    def _maybe_notify(self, scan, held) -> None:
        if not self.notify:
            self._held_prev = set(held)
            return
        new_buys = held - self._held_prev
        new_sells = self._held_prev - held
        signals = {r["symbol"] for r in scan if r["long"]} if not self.execute else set()
        if new_buys or new_sells or (not self.execute and signals != self._held_prev):
            try:
                from ..notify import build_from_env, notify_all
                if self._notifiers is None:
                    self._notifiers = build_from_env()
                parts = []
                if new_buys:
                    parts.append("🟢 买入 " + ", ".join(sorted(new_buys)))
                if new_sells:
                    parts.append("🔴 卖出 " + ", ".join(sorted(new_sells)))
                if not self.execute:
                    top = [r["symbol"] for r in sorted(scan, key=lambda x: x["strength"],
                           reverse=True) if r["long"]][:self.max_positions]
                    parts.append("建议持有(手动): " + ", ".join(top))
                if parts:
                    notify_all(f"【{self.mode} 组合信号变化】", "\n".join(parts), self._notifiers)
            except Exception as e:  # noqa: BLE001
                log.warning("组合通知失败: %s", e)
        self._held_prev = set(held) if self.execute else \
            {r["symbol"] for r in scan if r["long"]}

    def _write_state(self, scan, trades) -> dict:
        account = self.broker.get_account()
        prices = {r["symbol"]: r["price"] for r in scan}
        positions = []
        for sym, p in account.positions.items():
            if abs(p.amount) <= 1e-12:
                continue
            # 现价：合约优先用交易所【标记价】(与OKX App一致)，否则用扫描价
            mark = getattr(p, "mark_price", 0.0)
            px = mark or prices.get(sym, p.avg_price)
            is_long = p.amount > 0
            value = abs(p.amount) * px                       # 持仓金额(市值/名义, USDT)
            # 盈亏金额：合约用交易所口径浮动盈亏(和App一致)，否则按 带符号数量×(现价-成本)
            upnl = getattr(p, "unrealized_pnl", 0.0)
            pnl_amt = upnl or (p.amount * (px - p.avg_price))
            # 浮盈率：合约用交易所口径(含杠杆，和App一致)，否则用原始价差
            pct_exch = getattr(p, "pnl_pct_exch", 0.0)
            if pct_exch:
                pnl = pct_exch / 100.0
            elif p.avg_price:
                pnl = (px / p.avg_price - 1) if is_long else (p.avg_price / px - 1)
            else:
                pnl = 0.0
            # 每仓止损价(取当前已上移的实时止损线)/止盈价
            e = p.avg_price
            stop_px = self.stop_level(sym, e, is_long)
            if stop_px in (0.0, float("inf")):
                stop_px = None
            if self.take_profit:
                tgt_px = e * (1 + self.take_profit) if is_long else e * (1 - self.take_profit)
            else:
                tgt_px = None
            # 止损是否已到成本或更好(多≥成本/空≤成本) → 已保本/锁盈
            stop_locked = bool(stop_px and (
                (is_long and stop_px >= e) or (not is_long and stop_px <= e)))
            positions.append({
                "symbol": sym, "side": "long" if is_long else "short",
                "amount": round(abs(p.amount), 6),
                "avg_price": round(p.avg_price, 6), "price": round(px, 6),
                "value": round(value, 2), "pnl_amt": round(pnl_amt, 2),
                "pnl_pct": round(pnl * 100, 2),
                "stop_price": round(stop_px, 6) if stop_px else None,
                "target_price": round(tgt_px, 6) if tgt_px else None,
                "stop_locked": stop_locked,
            })
        # 合约：给持仓补上强平价 + 资金费率预警
        for p in positions:
            info = self._funding.get(p["symbol"])
            liq = self._liq.get(p["symbol"]) if hasattr(self, "_liq") else None
            if liq:
                p["liq_price"] = round(liq, 6)
                if p["price"]:
                    p["liq_dist_pct"] = round(abs(p["price"] - liq) / p["price"] * 100, 2)
            if info:
                p["funding_rate"] = info.get("rate")
                # 资金费方向：多头付费当 rate>0；空头付费当 rate<0（持仓与费率同号=付费）
                rate = info.get("rate") or 0.0
                paying = (p["side"] == "long" and rate > 0) or (p["side"] == "short" and rate < 0)
                p["funding_paying"] = bool(paying)

        top_scan = sorted(scan, key=lambda r: r["strength"], reverse=True)[:30]
        for r in top_scan:
            r["held"] = r["symbol"] in {p["symbol"] for p in positions}
        state = {
            "mode": self.mode, "market": self.market, "strategy": self.strategy.name,
            "running": True, "execute": self.execute, "demo": self.demo,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "equity": round(self.state.equity, 2),
            "cash": round(account.cash, 2),
            "max_positions": self.max_positions, "halted": self.risk.halted,
            "stop_loss_pct": round(self.stop_loss * 100, 1) if self.stop_loss else 0,
            "take_profit_pct": round(self.take_profit * 100, 1) if self.take_profit else 0,
            "trailing_stop_pct": round(self.trailing_stop * 100, 1) if self.trailing_stop else 0,
            "breakeven_pct": round(self.breakeven_trigger * 100, 1) if self.breakeven_trigger else 0,
            "atr_stop_mult": self.atr_stop_mult or 0,
            "cooldown": self.cooldown or 0,
            "market_gate": bool(self.market_gate),
            "regime": self._regime,     # +1多头/-1空头/0震荡(暂停开仓)/None(闸关或不适用)
            "regime_label": ({1: "多头行情·只做多", -1: "空头行情·只做空",
                              0: "震荡·暂停开新仓"}.get(self._regime, "—")
                             if self.market_gate and self.market == "crypto" else "—"),
            "timeframe": self.timeframe,
            "daily_halted": self.risk.daily_halted,
            "daily_loss_pct": self.risk.daily_loss_pct(self.state.equity),
            "max_daily_loss_pct": round(self.risk.cfg.max_daily_loss * 100, 1),
            "max_drawdown_pct": round(self.risk.cfg.max_portfolio_drawdown * 100, 1),
            "universe_size": len(self.universe), "scanned": len(scan),
            "positions": positions, "scan": top_scan,
            "recent_trades": list(reversed(self.state.recent_trades[-20:])),
            "live": self.journal.stats(),      # 真实资金曲线 + 胜率/盈亏比/回撤
        }
        _STATE_DIR.mkdir(exist_ok=True)
        (_STATE_DIR / f"portfolio_{self.mode}.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def run_forever(self) -> None:
        self.state.running = True
        log.info("组合引擎启动 [%s]：市场=%s 策略=%s universe=%d 最多持仓=%d 下单=%s demo=%s",
                 self.mode, self.market, self.strategy.name, len(self.universe),
                 self.max_positions, self.execute, self.demo)
        try:
            while self.state.running:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001
                    self._log_friendly_error(e)
                # 可中断睡眠：每秒检查一次停止标志，让"停止"按钮 ≤1 秒生效
                for _ in range(max(1, int(self.poll_seconds))):
                    if not self.state.running:
                        break
                    time.sleep(1)
        except KeyboardInterrupt:
            log.info("收到停止信号，组合引擎退出。")
        finally:
            self.state.running = False
            self._mark_stopped()

    def _mark_stopped(self) -> None:
        """引擎退出时把落盘状态标记为 running=False，让面板回到"启动"表单。"""
        path = _STATE_DIR / f"portfolio_{self.mode}.json"
        if not path.exists():
            return
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            d["running"] = False
            path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _log_friendly_error(e: Exception) -> None:
        """把常见错误翻译成一行人话，不吓唬新手（仍跳过本轮、继续运行）。"""
        msg = str(e)
        if any(k in msg for k in ("50113", "Invalid Sign", "Unauthorized", "401")):
            log.error("OKX 认证失败：请确认 .env 的 key/secret/passphrase 三项正确无空格，"
                      "且是【OKX 模拟交易】里生成的专用 API Key（实盘 key 不能配 OKX_DEMO=1）。"
                      "本轮跳过，会自动重试。")
        elif any(k in msg for k in ("getaddrinfo", "Failed to resolve",
                                    "NameResolution", "ConnectionError", "Max retries")):
            log.error("连不上 OKX：多半是代理没开。请确认 Clash 已开【系统代理】、"
                      "且 .env 里配了 HTTPS_PROXY。本轮跳过，会自动重试。")
        else:
            log.exception("组合 tick 异常，跳过本轮: %s", e)

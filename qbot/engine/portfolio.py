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
        stop_loss: float = 0.08,      # 硬止损：单仓亏损达此比例强制平仓(0=关)
        take_profit: float = 0.25,    # 硬止盈：单仓盈利达此比例强制平仓(0=关)
        trailing_stop: float = 0.0,   # 移动止损：从峰值回撤此比例平仓(0=关)
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
        self.state = PortfolioState()
        self._held_prev: set[str] = set()
        self._notifiers = None
        self._untradeable: set[str] = set()   # 记住下不了单的标的(如模拟盘不支持)，不再重试
        self.journal = TradeJournal(mode)     # 交易日志 + 真实资金曲线 + 实盘统计
        self._funding: dict = {}              # 合约资金费率缓存 {symbol: {...}}
        self._peak_pnl: dict = {}             # 移动止损：每仓自开仓以来的最佳浮盈率
        self._liq: dict = {}                  # 合约强平价缓存 {symbol: price}

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

    def tick(self) -> dict:
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
        else:
            # 只出信号不下单（A股实盘=手动）：用扫描结果推导"建议持有"清单
            pass

        self.state.ticks += 1
        self.state.equity = equity
        self._maybe_notify(scan, held)
        return self._write_state(scan, trades)

    def _apply_stops(self, prices, account, held, trades) -> None:
        """价格止损/止盈/移动止损：独立于信号，先于一切执行。
        这是"信号还没反转但价格已经打脸"时的硬保护——杠杆合约的保命线。"""
        for s in list(self._peak_pnl):        # 清理已平仓标的的峰值记录
            if s not in held:
                self._peak_pnl.pop(s, None)
        for sym in list(held):
            p = account.positions[sym]
            entry, px = p.avg_price, prices.get(sym)
            if not entry or not px or entry <= 0:
                continue
            is_long = p.amount > 0
            pnl = (px / entry - 1) if is_long else (entry / px - 1)   # 方向感知浮盈率
            best = max(self._peak_pnl.get(sym, pnl), pnl)
            self._peak_pnl[sym] = best
            reason = None
            if self.stop_loss and pnl <= -self.stop_loss:
                reason = f"止损{-self.stop_loss*100:.0f}%"
            elif self.take_profit and pnl >= self.take_profit:
                reason = f"止盈{self.take_profit*100:.0f}%"
            elif self.trailing_stop and best > 0 and (best - pnl) >= self.trailing_stop:
                reason = f"移动止损(峰值{best*100:.1f}%回撤{self.trailing_stop*100:.0f}%)"
            if reason:
                log.info("[%s] %s 触发%s（浮盈%.2f%%）→ 平仓", self.mode, sym, reason, pnl * 100)
                if self._close(sym, account, prices, trades, reason=reason):
                    held.discard(sym)
                    self._peak_pnl.pop(sym, None)

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

        # 2) 开仓：按共振分绝对值排序，填满剩余仓位（多头买入开、空头卖出开）
        weight = min(1.0 / self.max_positions, self.risk.cfg.max_position_per_symbol)
        candidates = sorted(
            (r for r in scan if abs(r["target"]) > 0.5 and r["symbol"] not in held
             and r["symbol"] not in self._untradeable),
            key=lambda r: abs(r["strength"]), reverse=True)
        for r in candidates:
            if len(held) >= self.max_positions:
                break
            sym, price = r["symbol"], r["price"]
            amt = (equity * weight) / price if price > 0 else 0.0
            if amt <= 0:
                continue
            side = "buy" if r["target"] > 0 else "sell"   # 卖出=开空
            if self._safe_submit(sym, side, amt, price, trades):
                held.add(sym)
        return trades

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
            px = prices.get(sym, p.avg_price)
            is_long = p.amount > 0
            # 方向感知的浮盈率：多头 (现价/成本-1)，空头 (成本/现价-1)
            if p.avg_price:
                pnl = (px / p.avg_price - 1) if is_long else (p.avg_price / px - 1)
            else:
                pnl = 0.0
            value = abs(p.amount) * px                       # 持仓金额(市值/名义, USDT)
            # 盈亏金额：合约优先用交易所给的浮动盈亏，否则按 带符号数量×(现价-成本)
            pnl_amt = getattr(p, "unrealized_pnl", 0.0) or (p.amount * (px - p.avg_price))
            # 每仓止损价/止盈价（按成本 + 方向算出的实际触发价）
            e = p.avg_price
            if is_long:
                stop_px = e * (1 - self.stop_loss) if self.stop_loss else None
                tgt_px = e * (1 + self.take_profit) if self.take_profit else None
            else:
                stop_px = e * (1 + self.stop_loss) if self.stop_loss else None
                tgt_px = e * (1 - self.take_profit) if self.take_profit else None
            positions.append({
                "symbol": sym, "side": "long" if is_long else "short",
                "amount": round(abs(p.amount), 6),
                "avg_price": round(p.avg_price, 6), "price": round(px, 6),
                "value": round(value, 2), "pnl_amt": round(pnl_amt, 2),
                "pnl_pct": round(pnl * 100, 2),
                "stop_price": round(stop_px, 6) if stop_px else None,
                "target_price": round(tgt_px, 6) if tgt_px else None,
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
            "daily_halted": self.risk.daily_halted,
            "daily_loss_pct": self.risk.daily_loss_pct(self.state.equity),
            "max_daily_loss_pct": round(self.risk.cfg.max_daily_loss * 100, 1),
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
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            log.info("收到停止信号，组合引擎退出。")
        finally:
            self.state.running = False

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

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
        self.state = PortfolioState()
        self._held_prev: set[str] = set()
        self._notifiers = None
        self._untradeable: set[str] = set()   # 记住下不了单的标的(如模拟盘不支持)，不再重试

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
                rows.append({"symbol": sym, "price": price,
                             "long": target > 0.5, "strength": round(strength, 4)})
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
        held = {s for s, p in account.positions.items() if p.amount > 0}

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

    def _rebalance(self, scan, prices, account, equity, held) -> list[dict]:
        trades = []
        # 组合回撤熔断：清仓并停开新仓
        if self.risk.halted:
            for sym in list(held):
                amt = account.positions[sym].amount
                self.broker.submit(Order(sym, "sell", amt))
                trades.append(self._trade("sell", sym, amt, prices[sym]))
            log.warning("组合回撤熔断已触发：全部清仓、停开新仓。")
            return trades

        long_syms = {r["symbol"] for r in scan if r["long"]}
        # 1) 卖出：持仓中信号已转空的（单个失败不影响其它）
        for sym in list(held):
            if sym not in long_syms:
                amt = account.positions[sym].amount
                if self._safe_submit(sym, "sell", amt, prices[sym], trades):
                    held.discard(sym)

        # 2) 买入：按强度排序，填满剩余仓位（跳过下不了单/已知不可交易的标的）
        weight = min(1.0 / self.max_positions, self.risk.cfg.max_position_per_symbol)
        candidates = sorted(
            (r for r in scan if r["long"] and r["symbol"] not in held
             and r["symbol"] not in self._untradeable),
            key=lambda r: r["strength"], reverse=True)
        slots = self.max_positions - len(held)
        for r in candidates:
            if len(held) >= self.max_positions:
                break
            sym, price = r["symbol"], r["price"]
            amt = (equity * weight) / price if price > 0 else 0.0
            if amt <= 0:
                continue
            if self._safe_submit(sym, "buy", amt, price, trades):
                held.add(sym)
        return trades

    def _safe_submit(self, sym, side, amt, price, trades) -> bool:
        """下单，失败只跳过该标的、不中断整轮。返回是否成功。"""
        try:
            self.broker.submit(Order(sym, side, amt))
            trades.append(self._trade(side, sym, amt, price))
            return True
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "does not have market symbol" in msg or "BadSymbol" in msg or "51001" in msg:
                self._untradeable.add(sym)   # 该标的（模拟盘）不可交易，记下不再重试
                log.warning("%s 不可交易（%s模拟盘可能不支持），已跳过。", sym, "OKX")
            else:
                log.warning("%s 下单失败：%s（跳过）", sym, msg[:100])
            return False

    def _trade(self, side, sym, amt, price) -> dict:
        rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "side": side, "symbol": sym, "amount": round(amt, 6),
               "price": round(price, 6)}
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
            if p.amount <= 0:
                continue
            px = prices.get(sym, p.avg_price)
            positions.append({
                "symbol": sym, "amount": round(p.amount, 6),
                "avg_price": round(p.avg_price, 6), "price": round(px, 6),
                "pnl_pct": round((px / p.avg_price - 1) * 100, 2) if p.avg_price else 0.0,
            })
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
            "universe_size": len(self.universe), "scanned": len(scan),
            "positions": positions, "scan": top_scan,
            "recent_trades": list(reversed(self.state.recent_trades[-20:])),
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

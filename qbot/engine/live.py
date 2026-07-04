"""实盘/模拟盘引擎：24 小时循环的心脏。

每个周期做的事（和回测逻辑一一对应，保证一致性）：
  1. 拉最新行情
  2. 策略算目标仓位
  3. 风控审核（可否决、可收敛仓位、可熔断）
  4. 计算目标持仓与当前持仓的差额，下单补齐
  5. 记录状态，推给面板

设计成"每次 tick 一步"，既能被 while 循环驱动做真 24h 运行，
也能被面板/测试单步调用，方便观察。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..broker.base import Broker, Order
from ..data.loader import get_ohlcv
from ..logger import get_logger
from ..risk.manager import RiskManager
from ..strategies.base import Strategy

log = get_logger("qbot.engine")


@dataclass
class EngineState:
    running: bool = False
    last_signal: float = 0.0
    last_price: float = 0.0
    equity: float = 0.0
    halted: bool = False
    ticks: int = 0
    history: list = field(default_factory=list)   # [(ts, equity, price, position)]


class LiveEngine:
    def __init__(
        self,
        market: str,
        symbol: str,
        strategy: Strategy,
        broker: Broker,
        risk: RiskManager,
        timeframe: str = "1d",
        lookback: int = 300,
        poll_seconds: int = 60,
        demo: bool = True,
        atr_stop_mult: float | None = None,
        notify: bool = False,
    ):
        self.market = market
        self.symbol = symbol
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self.timeframe = timeframe
        self.lookback = lookback
        self.poll_seconds = poll_seconds
        self.demo = demo
        self.atr_stop_mult = atr_stop_mult
        self.notify = notify
        self._notifiers = None
        self._last_bucket: str | None = None
        self.state = EngineState()

    def _maybe_notify(self, target_pos: float, price: float) -> None:
        """信号档位变化时推送（best-effort，绝不阻塞交易）。首次只记基线。"""
        if not self.notify:
            return
        bucket = "long" if target_pos > 0.5 else ("short" if target_pos < -0.5 else "flat")
        if self._last_bucket is not None and bucket != self._last_bucket:
            try:
                from ..notify import build_from_env, notify_all
                if self._notifiers is None:
                    self._notifiers = build_from_env()
                label = {"long": "🟢 买入/持有", "short": "🔴 做空", "flat": "⚪ 空仓/观望"}
                notify_all(
                    f"【信号变化】{self.symbol} {label[self._last_bucket]} → {label[bucket]}",
                    f"策略: {self.strategy.name}\n最新价: {price}\n目标仓位: {target_pos:.2f}",
                    self._notifiers,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("信号通知失败: %s", e)
        self._last_bucket = bucket

    def _target_from_strategy(self, df):
        """与回测完全一致的目标仓位计算（含可选 ATR 移动止损）。"""
        pos = self.strategy.generate_positions(df).fillna(0.0)
        if self.strategy.long_only:
            pos = pos.clip(lower=0.0)
        if self.atr_stop_mult:
            from ..risk.stops import apply_atr_trailing_stop
            pos = apply_atr_trailing_stop(df, pos, self.atr_stop_mult)
        return float(pos.iloc[-1]) if len(pos) else 0.0

    def tick(self) -> EngineState:
        """执行一个决策周期。返回最新状态。"""
        df = get_ohlcv(self.market, self.symbol, self.timeframe,
                       limit=self.lookback, demo=self.demo)
        price = float(df["close"].iloc[-1])
        raw_target = self._target_from_strategy(df)
        self._maybe_notify(raw_target, price)

        # 把最新价喂给模拟盘
        if hasattr(self.broker, "set_price"):
            self.broker.set_price(self.symbol, price)

        account = self.broker.get_account()
        equity = account.equity({self.symbol: price})

        decision = self.risk.evaluate(raw_target, equity)
        target_pos = decision.adjusted_position
        if not decision.allow:
            log.warning("风控否决: %s", decision.reason)
            target_pos = 0.0

        self._rebalance(target_pos, price, equity, account)

        self.state.last_signal = target_pos
        self.state.last_price = price
        self.state.equity = equity
        self.state.halted = self.risk.halted
        self.state.ticks += 1
        self.state.history.append(
            (str(df.index[-1]), equity, price, target_pos)
        )
        log.info("tick #%d | %s 价=%.4f 目标仓位=%.2f 净值=%.2f %s",
                 self.state.ticks, self.symbol, price, target_pos, equity,
                 "[熔断]" if self.risk.halted else "")
        return self.state

    def _rebalance(self, target_pos: float, price: float, equity: float, account) -> None:
        """把当前持仓调整到目标仓位（target_pos 是占总资金比例）。"""
        pos = account.positions.get(self.symbol)
        cur_amt = pos.amount if pos else 0.0
        target_value = equity * target_pos
        target_amt = target_value / price if price > 0 else 0.0
        delta = target_amt - cur_amt
        if abs(delta * price) < max(1.0, equity * 0.001):  # 差额太小不折腾
            return
        side = "buy" if delta > 0 else "sell"
        self.broker.submit(Order(self.symbol, side, abs(delta)))

    def run_forever(self) -> None:
        """真正的 24h 循环。生产环境建议配合进程守护（systemd/supervisor）。"""
        self.state.running = True
        log.info("引擎启动: %s/%s 策略=%s 周期=%ds demo=%s",
                 self.market, self.symbol, self.strategy.name,
                 self.poll_seconds, self.demo)
        try:
            while self.state.running:
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001  单周期出错不该弄挂整个引擎
                    log.exception("tick 异常，跳过本周期: %s", e)
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            log.info("收到停止信号，引擎退出。")
        finally:
            self.state.running = False

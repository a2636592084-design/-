"""模拟盘下单通道。用真实行情价成交，但用虚拟资金——练手、验证系统的最佳方式。

铁律：任何策略先在 PaperBroker 上稳定盈利若干周期，才允许切实盘。
"""
from __future__ import annotations

from ..logger import get_logger
from .base import Account, Broker, Fill, Order, Position

log = get_logger("qbot.broker.paper")


class PaperBroker(Broker):
    def __init__(self, cash: float = 100_000.0, fee_rate: float = 0.0005,
                 slippage: float = 0.0005):
        self.account = Account(cash=cash)
        self.fee_rate = fee_rate
        self.slippage = slippage
        self._last_price: dict[str, float] = {}
        self.fills: list[Fill] = []

    def set_price(self, symbol: str, price: float) -> None:
        """引擎每个周期把最新价喂进来。"""
        self._last_price[symbol] = price

    def market_price(self, symbol: str) -> float:
        return self._last_price.get(symbol, 0.0)

    def submit(self, order: Order) -> Fill:
        px = order.price or self.market_price(order.symbol)
        # 市价单加滑点：买贵一点、卖便宜一点，贴近真实
        if order.side == "buy":
            px *= (1 + self.slippage)
        else:
            px *= (1 - self.slippage)
        notional = px * order.amount
        fee = notional * self.fee_rate

        pos = self.account.positions.get(order.symbol, Position(order.symbol))
        if order.side == "buy":
            self.account.cash -= notional + fee
            new_amt = pos.amount + order.amount
            pos.avg_price = (
                (pos.avg_price * pos.amount + notional) / new_amt if new_amt else 0.0
            )
            pos.amount = new_amt
        else:
            self.account.cash += notional - fee
            new_amt = pos.amount - order.amount
            # 卖出使净仓变为/维持负数 = 开空/加空：记录做空均价（浮盈显示用）
            if new_amt < 0:
                pos.avg_price = px if pos.amount >= 0 else (
                    (pos.avg_price * (-pos.amount) + notional) / (-new_amt))
            pos.amount = new_amt
            if abs(pos.amount) < 1e-9:
                pos.amount = 0.0
        self.account.positions[order.symbol] = pos

        fill = Fill(order.symbol, order.side, order.amount, px, fee)
        self.fills.append(fill)
        log.info("模拟成交 %s %s %.6f @ %.4f (fee=%.4f)",
                 order.side, order.symbol, order.amount, px, fee)
        return fill

    def get_account(self) -> Account:
        return self.account

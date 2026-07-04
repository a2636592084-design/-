"""下单通道抽象。回测/模拟/实盘统一接口，切换只改一行配置。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Order:
    symbol: str
    side: str          # 'buy' | 'sell'
    amount: float      # 数量（币/股）
    price: float | None = None   # None=市价
    type: str = "market"


@dataclass
class Fill:
    symbol: str
    side: str
    amount: float
    price: float
    fee: float
    ts: str = ""


@dataclass
class Position:
    symbol: str
    amount: float = 0.0
    avg_price: float = 0.0


@dataclass
class Account:
    cash: float
    positions: dict = field(default_factory=dict)
    # 合约保证金账户直接给出总权益(USDT)；现货/模拟盘留 None，按 现金+持仓市值 计算
    equity_override: float | None = None

    def equity(self, prices: dict) -> float:
        if self.equity_override is not None:
            return self.equity_override
        val = self.cash
        for sym, pos in self.positions.items():
            val += pos.amount * prices.get(sym, pos.avg_price)
        return val


class Broker:
    """所有下单通道的基类。"""

    def market_price(self, symbol: str) -> float:
        raise NotImplementedError

    def submit(self, order: Order) -> Fill:
        raise NotImplementedError

    def get_account(self) -> Account:
        raise NotImplementedError

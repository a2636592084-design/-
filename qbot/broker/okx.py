"""OKX 实盘/模拟盘下单通道（通过 ccxt）。

⚠️ 真钱警告：把 demo=False 才是真实资金。请务必：
   1. 先在 OKX 模拟盘（demo=True）跑通全流程；
   2. 用最小额度实盘验证；
   3. 确认风控熔断有效后再逐步加仓。
OKX 官方模拟盘申请：账户 -> 模拟交易 -> 生成模拟盘 API Key。
"""
from __future__ import annotations

from ..logger import get_logger
from .base import Account, Broker, Fill, Order, Position

log = get_logger("qbot.broker.okx")


class OKXBroker(Broker):
    def __init__(self, api_key: str, secret: str, passphrase: str, demo: bool = True):
        import ccxt

        self.demo = demo
        self.exchange = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
            "enableRateLimit": True,
        })
        # 关键：下单/查账户也必须走代理，否则国内直连 www.okx.com 会 DNS 失败。
        # 复用行情层已验证的代理/CA 配置。
        from ..data.crypto import _apply_proxy
        _apply_proxy(self.exchange)
        # OKX 模拟盘不支持 asset/currencies 接口（会报 50038）。关闭 fetchCurrencies，
        # 让 load_markets 跳过它——查账户/下单本身在模拟盘完全可用。
        self.exchange.has["fetchCurrencies"] = False
        if demo:
            self.exchange.headers = {"x-simulated-trading": "1"}
            log.warning("OKX 处于【模拟盘】模式（demo=True），不会动用真钱。")
        else:
            log.warning("OKX 处于【实盘】模式（demo=False）——真实资金，请谨慎！")

    def market_price(self, symbol: str) -> float:
        return float(self.exchange.fetch_ticker(symbol)["last"])

    def submit(self, order: Order) -> Fill:
        result = self.exchange.create_order(
            symbol=order.symbol,
            type=order.type,
            side=order.side,
            amount=order.amount,
            price=order.price,
        )
        px = float(result.get("average") or result.get("price") or self.market_price(order.symbol))
        fee = float((result.get("fee") or {}).get("cost") or 0.0)
        log.info("OKX 下单结果: %s", result.get("id"))
        return Fill(order.symbol, order.side, order.amount, px, fee)

    def get_account(self) -> Account:
        bal = self.exchange.fetch_balance()
        cash = float(bal.get("USDT", {}).get("free", 0.0))
        positions: dict[str, Position] = {}
        for coin, info in bal.get("total", {}).items():
            if coin == "USDT" or not info:
                continue
            positions[f"{coin}/USDT"] = Position(f"{coin}/USDT", amount=float(info))
        return Account(cash=cash, positions=positions)

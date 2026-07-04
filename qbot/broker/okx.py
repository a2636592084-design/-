"""OKX 下单通道（通过 ccxt）。支持现货(spot)与合约/永续(swap)。

合约（swap）要点：
  · 逐仓(isolated) + 单向净持仓 + 默认 3x 杠杆（可配）。
  · 引擎传来的"币数量"会换算成合约"张数"（用市场 contractSize）。
  · 做多=buy 开、做空=sell 开；平仓=反向等量单（单向净持仓下自动减仓）。
  · 持仓用 fetch_positions 读，带方向；总权益直接取账户 USDT 总额。

⚠️ 真钱警告：demo=False 才是真实资金；合约带杠杆会爆仓。请先在 OKX 模拟盘跑稳。
   OKX 账户请设为【单向持仓】模式；模拟盘 API Key 需在【模拟交易】里生成。
"""
from __future__ import annotations

from ..logger import get_logger
from .base import Account, Broker, Fill, Order, Position

log = get_logger("qbot.broker.okx")


class OKXBroker(Broker):
    def __init__(self, api_key: str, secret: str, passphrase: str, demo: bool = True,
                 trade_type: str = "spot", leverage: int = 3,
                 margin_mode: str = "isolated"):
        import ccxt

        self.demo = demo
        self.trade_type = trade_type          # 'spot' 或 'swap'
        self.leverage = leverage
        self.margin_mode = margin_mode
        self.exchange = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": trade_type},
        })
        from ..data.crypto import _apply_proxy
        _apply_proxy(self.exchange)
        self.exchange.has["fetchCurrencies"] = False   # 模拟盘不支持 asset/currencies
        if demo:
            self.exchange.headers = {"x-simulated-trading": "1"}
            log.warning("OKX【模拟盘】(demo=True) · %s · 不动真钱。", trade_type)
        else:
            log.warning("OKX【实盘】(demo=False) · %s · 真实资金，谨慎！", trade_type)
        if trade_type == "swap":
            log.warning("合约模式：%dx 杠杆 · %s · 请确认 OKX 账户为【单向持仓】。",
                        leverage, margin_mode)
        self._lev_set: set[str] = set()

    def market_price(self, symbol: str) -> float:
        return float(self.exchange.fetch_ticker(symbol)["last"])

    def _ensure_leverage(self, symbol: str) -> None:
        if self.trade_type != "swap" or symbol in self._lev_set:
            return
        try:
            self.exchange.set_leverage(self.leverage, symbol,
                                       params={"mgnMode": self.margin_mode})
        except Exception as e:  # noqa: BLE001
            log.warning("设置 %s 杠杆失败(%s)，用账户当前杠杆继续。", symbol, str(e)[:80])
        self._lev_set.add(symbol)

    def submit(self, order: Order) -> Fill:
        if self.trade_type == "swap":
            self._ensure_leverage(order.symbol)
            market = self.exchange.market(order.symbol)
            csize = float(market.get("contractSize") or 1.0)
            contracts = order.amount / csize if csize else order.amount
            contracts = float(self.exchange.amount_to_precision(order.symbol, contracts))
            if contracts <= 0:
                raise ValueError(f"{order.symbol} 换算张数为0(可能低于最小下单量)")
            result = self.exchange.create_order(
                order.symbol, "market", order.side, contracts,
                params={"tdMode": self.margin_mode})
        else:
            result = self.exchange.create_order(
                order.symbol, order.type, order.side, order.amount, order.price)
        px = float(result.get("average") or result.get("price")
                   or self.market_price(order.symbol))
        fee = float((result.get("fee") or {}).get("cost") or 0.0)
        log.info("OKX 下单: %s %s %s -> id=%s", self.trade_type, order.side,
                 order.symbol, result.get("id"))
        return Fill(order.symbol, order.side, order.amount, px, fee)

    def get_account(self) -> Account:
        bal = self.exchange.fetch_balance()
        free = float(bal.get("USDT", {}).get("free", 0.0) or 0.0)
        if self.trade_type != "swap":
            positions: dict[str, Position] = {}
            for coin, info in bal.get("total", {}).items():
                if coin == "USDT" or not info:
                    continue
                positions[f"{coin}/USDT"] = Position(f"{coin}/USDT", amount=float(info))
            return Account(cash=free, positions=positions)

        # 合约：持仓用 fetch_positions，带方向；总权益取账户 USDT 总额
        total = float(bal.get("USDT", {}).get("total", free) or free)
        positions = {}
        try:
            for p in self.exchange.fetch_positions():
                contracts = float(p.get("contracts") or 0.0)
                if contracts <= 0:
                    continue
                sym = p.get("symbol")
                csize = float((p.get("info") or {}).get("ctVal")
                              or self.exchange.market(sym).get("contractSize") or 1.0)
                base = contracts * csize
                signed = base if p.get("side") == "long" else -base
                positions[sym] = Position(
                    sym, amount=signed, avg_price=float(p.get("entryPrice") or 0.0))
        except Exception as e:  # noqa: BLE001
            log.warning("读取合约持仓失败: %s", str(e)[:100])
        return Account(cash=free, positions=positions, equity_override=total)

"""从一个配置字典构建 PortfolioEngine —— 命令行(run_portfolio.py)与网页面板共用。

这样"点按钮启动"和"命令行启动"走的是同一套构建逻辑，行为一致、不会两套代码走偏。
"""
from __future__ import annotations

from ..broker.paper import PaperBroker
from ..config import AppConfig
from ..risk.manager import RiskConfig, RiskManager
from ..strategies import REGISTRY
from ..universe import get_universe
from .portfolio import PortfolioEngine

# 面板默认值（对新手友好、偏稳健）
DEFAULTS = {
    "mode": "paper", "market": "crypto", "broker": "paper", "trade_type": "spot",
    "leverage": 3, "allow_short": False, "strategy": "confluence",
    "quote": "USDT", "types": None,
    "top": 40, "max_positions": 6, "timeframe": "4h", "poll": 60,
    "stop_loss": 0.08, "take_profit": 0.0, "breakeven": 0.05, "trailing_stop": 0.06,
    "atr_stop_mult": 2.5, "cooldown": 3, "exchange_stops": True, "quality": True,
    "market_gate": False, "gate_adx": 20.0, "gate_ema": 200,
    "notify": False, "i_understand_risk": False, "capital": 100_000.0,
}


def build_engine(cfg: dict) -> tuple[PortfolioEngine, dict]:
    """按配置造好引擎。出错抛 ValueError（消息是给用户看的人话）。"""
    c = {**DEFAULTS, **(cfg or {})}

    if c["strategy"] not in REGISTRY:
        raise ValueError(f"未知策略 {c['strategy']}")
    strat = (REGISTRY[c["strategy"]](allow_short=True)
             if (c["allow_short"] and c["strategy"] == "confluence")
             else REGISTRY[c["strategy"]]())
    risk = RiskManager(RiskConfig())

    market = c["market"]
    execute = market != "ashare"          # A股只出信号、永不自动下单

    types = tuple(c["types"]) if c["types"] else \
        (("swap",) if c["trade_type"] == "swap" else ("spot",))
    universe = get_universe(market, top=int(c["top"]), types=types, quote=c["quote"],
                            quality=bool(c["quality"]))
    if not universe:
        raise ValueError("未获取到任何标的：请检查网络/代理（Clash 是否开着）。")

    demo = True
    if c["broker"] == "okx":
        creds = AppConfig.load().okx
        if not creds.configured:
            raise ValueError("未配置 OKX API：请在 .env 填 OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE。")
        if not creds.demo and not c["i_understand_risk"]:
            raise ValueError("检测到【实盘】(OKX_DEMO=0)。请勾选“我已了解真钱风险”再启动。")
        from ..broker.okx import OKXBroker
        broker = OKXBroker(creds.api_key, creds.secret, creds.passphrase, demo=creds.demo,
                           trade_type=c["trade_type"], leverage=int(c["leverage"]),
                           margin_mode="isolated")
        demo = creds.demo
    else:
        broker = PaperBroker(cash=float(c["capital"]))

    engine = PortfolioEngine(
        mode=c["mode"], market=market, universe=universe, strategy=strat,
        broker=broker, risk=risk, max_positions=int(c["max_positions"]),
        timeframe=c["timeframe"], poll_seconds=int(c["poll"]), demo=demo,
        execute=execute, notify=bool(c["notify"]),
        stop_loss=float(c["stop_loss"]), take_profit=float(c["take_profit"]),
        trailing_stop=float(c["trailing_stop"]), breakeven_trigger=float(c["breakeven"]),
        atr_stop_mult=float(c["atr_stop_mult"]), cooldown=int(c["cooldown"]),
        exchange_stops=bool(c["exchange_stops"]),
        market_gate=bool(c["market_gate"]), gate_adx=float(c["gate_adx"]),
        gate_ema=int(c["gate_ema"]),
    )
    info = {
        "mode": c["mode"], "market": market, "broker": c["broker"],
        "trade_type": c["trade_type"], "demo": demo, "execute": execute,
        "universe_size": len(universe), "allow_short": bool(c["allow_short"]),
        "leverage": int(c["leverage"]), "config": c,
    }
    return engine, info

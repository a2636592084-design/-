#!/usr/bin/env python3
"""启动实盘/模拟盘引擎（24 小时循环）。

默认安全配置：模拟盘（PaperBroker）+ 保守风控。
    python run_live.py --market crypto --symbol BTC/USDT --strategy donchian

接 OKX 模拟盘（需在 .env 配好模拟盘 API key，OKX_DEMO=1）：
    python run_live.py --broker okx --market crypto --symbol BTC/USDT --strategy donchian

⚠️ 切真实资金：在 .env 设 OKX_DEMO=0，并加 --i-understand-the-risk。请务必先在
   模拟盘跑稳、且看懂 risk/manager.py 的每一道风控后再动真钱。
"""
from __future__ import annotations

import argparse
import sys

from qbot.broker.paper import PaperBroker
from qbot.config import AppConfig
from qbot.engine.live import LiveEngine
from qbot.risk.manager import RiskConfig, RiskManager
from qbot.strategies import REGISTRY


def main() -> None:
    p = argparse.ArgumentParser(description="qbot 实盘/模拟盘引擎")
    p.add_argument("--market", default="crypto", choices=["crypto", "ashare", "synthetic"])
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--strategy", default="donchian", choices=list(REGISTRY))
    p.add_argument("--broker", default="paper", choices=["paper", "okx"])
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--poll", type=int, default=60, help="轮询间隔秒")
    p.add_argument("--once", action="store_true", help="只跑一个 tick 后退出（调试用）")
    p.add_argument("--i-understand-the-risk", action="store_true",
                   help="真实资金必需的显式确认")
    args = p.parse_args()

    cfg = AppConfig.load()
    strat = REGISTRY[args.strategy]()
    risk = RiskManager(RiskConfig())

    if args.broker == "okx":
        creds = cfg.okx
        if not creds.configured:
            sys.exit("未配置 OKX API（请在 .env 填 OKX_API_KEY/SECRET/PASSPHRASE）")
        if not creds.demo and not args.i_understand_the_risk:
            sys.exit("检测到实盘模式(OKX_DEMO=0)但缺少 --i-understand-the-risk，已中止保护你。")
        from qbot.broker.okx import OKXBroker
        broker = OKXBroker(creds.api_key, creds.secret, creds.passphrase, demo=creds.demo)
        demo = creds.demo
    else:
        broker = PaperBroker(cash=args.capital)
        demo = True

    engine = LiveEngine(
        market=args.market, symbol=args.symbol, strategy=strat,
        broker=broker, risk=risk, timeframe=args.timeframe,
        poll_seconds=args.poll, demo=demo,
    )

    if args.once:
        state = engine.tick()
        print(f"单 tick 完成：价={state.last_price:.4f} 目标仓位={state.last_signal:.2f} "
              f"净值={state.equity:.2f}")
    else:
        engine.run_forever()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""全市场组合引擎：扫描全部标的，择优持有有限个仓位。

用法：
    # 模拟盘·加密全市场(现货Top60)·共振策略，先跑一轮看看
    python run_portfolio.py --mode paper --market crypto --strategy confluence --top 60 --once

    # A股实盘=信号+手动(不下单，只出建议持有清单)
    python run_portfolio.py --mode ashare --market ashare --strategy confluence --top 80 --signals-only

    # 加密现货+合约一起扫（现货+永续）
    python run_portfolio.py --mode crypto --market crypto --types spot swap --top 80

⚠️ 真钱：--broker okx 且 .env 里 OKX_DEMO=0 才动真钱，需加 --i-understand-the-risk。
"""
from __future__ import annotations

import argparse
import sys

from qbot.broker.paper import PaperBroker
from qbot.config import AppConfig
from qbot.engine.portfolio import PortfolioEngine
from qbot.risk.manager import RiskConfig, RiskManager
from qbot.strategies import REGISTRY
from qbot.universe import get_universe


def main() -> None:
    p = argparse.ArgumentParser(description="qbot 全市场组合引擎")
    p.add_argument("--mode", default="paper", help="展示标签：paper/ashare/crypto")
    p.add_argument("--market", default="crypto", choices=["crypto", "ashare", "synthetic"])
    p.add_argument("--types", nargs="+", default=None,
                   help="加密标的类型：spot swap future（默认随 --trade-type）")
    p.add_argument("--quote", default="USDT")
    p.add_argument("--strategy", default="confluence", choices=list(REGISTRY))
    p.add_argument("--broker", default="paper", choices=["paper", "okx"])
    p.add_argument("--trade-type", default="spot", choices=["spot", "swap"],
                   help="现货 spot 或 合约/永续 swap")
    p.add_argument("--leverage", type=int, default=3, help="合约杠杆(默认3)")
    p.add_argument("--allow-short", action="store_true", help="允许做空(双向，合约用)")
    p.add_argument("--top", type=int, default=60, help="扫描标的数上限(按成交额)")
    p.add_argument("--max-positions", type=int, default=12, help="最多同时持仓数")
    p.add_argument("--stop-loss", type=float, default=0.08, help="硬止损比例(默认8%%，0=关)")
    p.add_argument("--take-profit", type=float, default=0.25, help="硬止盈比例(默认25%%，0=关)")
    p.add_argument("--trailing-stop", type=float, default=0.0,
                   help="移动止损：止损跟随峰值留此回撤空间(默认关，如 0.05=留5%%)")
    p.add_argument("--breakeven", type=float, default=0.0,
                   help="保本上移：盈利达此比例即把止损抬到成本(默认关，如 0.05)")
    p.add_argument("--atr-stop", type=float, default=0.0,
                   help="ATR自适应止损倍数(>0则覆盖固定止损、并按波动定仓位，如 2.5)")
    p.add_argument("--cooldown", type=int, default=0,
                   help="被止损后隔多少个扫描周期才允许再进该标的(默认0=关，如 3)")
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--poll", type=int, default=300)
    p.add_argument("--once", action="store_true", help="扫一轮后退出")
    p.add_argument("--signals-only", action="store_true",
                   help="只出信号不下单（A股实盘=手动）")
    p.add_argument("--notify", action="store_true")
    p.add_argument("--i-understand-the-risk", action="store_true")
    args = p.parse_args()

    cfg = AppConfig.load()
    # 双向做空：confluence 支持 allow_short
    if args.allow_short and args.strategy == "confluence":
        strat = REGISTRY[args.strategy](allow_short=True)
    else:
        strat = REGISTRY[args.strategy]()
    risk = RiskManager(RiskConfig())

    # 标的类型默认随 trade-type：合约取永续、现货取现货
    types = tuple(args.types) if args.types else \
        (("swap",) if args.trade_type == "swap" else ("spot",))
    print(f"列出 {args.market} 全市场标的（Top {args.top} · {'/'.join(types)}）...")
    universe = get_universe(args.market, top=args.top, types=types, quote=args.quote)
    if not universe:
        sys.exit("未获取到任何标的，请检查网络/代理。")

    execute = not args.signals_only and args.market != "ashare" or \
        (args.market == "ashare" and False)  # A股永远不自动下单
    if args.market == "ashare":
        execute = False

    if args.broker == "okx":
        creds = cfg.okx
        if not creds.configured:
            sys.exit("未配置 OKX API（.env 填 OKX_API_KEY/SECRET/PASSPHRASE）")
        if not creds.demo and not args.i_understand_the_risk:
            sys.exit("检测到实盘(OKX_DEMO=0)但缺 --i-understand-the-risk，已中止保护你。")
        from qbot.broker.okx import OKXBroker
        broker = OKXBroker(creds.api_key, creds.secret, creds.passphrase, demo=creds.demo,
                           trade_type=args.trade_type, leverage=args.leverage,
                           margin_mode="isolated")
        demo = creds.demo
    else:
        broker = PaperBroker(cash=args.capital)
        demo = True

    engine = PortfolioEngine(
        mode=args.mode, market=args.market, universe=universe, strategy=strat,
        broker=broker, risk=risk, max_positions=args.max_positions,
        timeframe=args.timeframe, poll_seconds=args.poll, demo=demo,
        execute=execute, notify=args.notify,
        stop_loss=args.stop_loss, take_profit=args.take_profit,
        trailing_stop=args.trailing_stop, breakeven_trigger=args.breakeven,
        atr_stop_mult=args.atr_stop, cooldown=args.cooldown,
    )

    if args.once:
        st = engine.tick()
        print(f"\n扫描 {st['scanned']}/{st['universe_size']} 个标的 · "
              f"持仓 {len(st['positions'])}/{args.max_positions} · 净值 {st['equity']} · "
              f"{'仅信号(手动下单)' if not execute else '已自动下单'}")
        sigs = [f"{r['symbol']}{'🟢多' if r.get('long') else '🔴空'}({r['strength']:+.2f})"
                for r in st["scan"][:10] if r.get("long") or r.get("short")]
        print("Top 信号：", ", ".join(sigs) or "（当前无做多/做空信号）")
    else:
        engine.run_forever()


if __name__ == "__main__":
    main()

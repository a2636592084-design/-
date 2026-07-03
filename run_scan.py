#!/usr/bin/env python3
"""信号扫描器 —— A股"数据+信号"路线的核心工具。

批量扫描一篮子标的，对每个标的用指定策略算出【当前最新信号】，
输出一张清单：哪些该买、哪些该卖/空仓。你据此手动或半自动下单。

用法：
    # 扫描几只 A股（默认策略 ma_cross）
    python run_scan.py --market ashare --symbols 600519 000001 300750 --strategy ma_cross

    # 扫描几个加密标的
    python run_scan.py --market crypto --symbols BTC/USDT ETH/USDT SOL/USDT --strategy donchian

    # 离线演示
    python run_scan.py --market synthetic --symbols DEMO1 DEMO2 --strategy rsi_reversion
"""
from __future__ import annotations

import argparse

from qbot.data.loader import get_ohlcv
from qbot.strategies import REGISTRY


def label(pos: float) -> str:
    if pos > 0.5:
        return "🟢 买入/持有"
    if pos < -0.5:
        return "🔴 做空"
    return "⚪ 空仓/观望"


def main() -> None:
    p = argparse.ArgumentParser(description="qbot 信号扫描")
    p.add_argument("--market", default="synthetic", choices=["synthetic", "crypto", "ashare"])
    p.add_argument("--symbols", nargs="+", default=["DEMO1", "DEMO2", "DEMO3"])
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--strategy", default="ma_cross", choices=list(REGISTRY))
    p.add_argument("--limit", type=int, default=400)
    args = p.parse_args()

    strat = REGISTRY[args.strategy]()
    print(f"\n扫描时间粒度={args.timeframe} 策略={args.strategy} 市场={args.market}")
    print("-" * 60)
    print(f"{'标的':<14}{'最新价':>12}{'目标仓位':>10}   信号")
    print("-" * 60)

    for i, sym in enumerate(args.symbols):
        # 合成市场给不同标的不同种子，避免每个都一样
        df = get_ohlcv(args.market, sym, args.timeframe, limit=args.limit)
        if args.market == "synthetic":
            from qbot.data.synthetic import synthetic_ohlcv
            df = synthetic_ohlcv(periods=args.limit, seed=42 + i)
        sig = strat.latest_signal(df)
        price = float(df["close"].iloc[-1])
        print(f"{sym:<14}{price:>12.4f}{sig.target_position:>10.2f}   {label(sig.target_position)}")

    print("-" * 60)
    print("提示：信号仅供参考，务必结合风控与你自己的判断。此工具不自动下单。\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""回测 CLI。

用法示例：
    # 用合成数据快速验证（无需联网/API）
    python run_backtest.py --market synthetic --strategy ma_cross

    # 回测比特币日线（需装 ccxt 且能连 OKX）
    python run_backtest.py --market crypto --symbol BTC/USDT --timeframe 1d --strategy donchian

    # 回测贵州茅台（需装 akshare）
    python run_backtest.py --market ashare --symbol 600519 --strategy rsi_reversion
"""
from __future__ import annotations

import argparse

from qbot.backtest import Backtester
from qbot.data.loader import get_ohlcv
from qbot.strategies import REGISTRY


def main() -> None:
    p = argparse.ArgumentParser(description="qbot 回测")
    p.add_argument("--market", default="synthetic",
                   choices=["synthetic", "crypto", "ashare"])
    p.add_argument("--symbol", default="DEMO")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--limit", type=int, default=1500)
    p.add_argument("--strategy", default="ma_cross", choices=list(REGISTRY))
    p.add_argument("--capital", type=float, default=100_000.0)
    p.add_argument("--fee", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--ppy", type=int, default=252,
                   help="每年周期数：日线股票252，加密日线可用365")
    args = p.parse_args()

    df = get_ohlcv(args.market, args.symbol, args.timeframe, limit=args.limit)
    strat = REGISTRY[args.strategy]()
    bt = Backtester(args.capital, args.fee, args.slippage, args.ppy)
    result = bt.run(df, strat)

    print("=" * 44)
    print(f"  市场={args.market}  标的={args.symbol}  策略={args.strategy}")
    print(f"  样本={len(df)} 根K线  {df.index[0].date()} ~ {df.index[-1].date()}")
    print("=" * 44)
    print(result.summary())
    print("=" * 44)
    # 存一份资金曲线，方便面板/复盘
    out = result.equity.rename("equity").to_frame()
    out["price"] = result.price
    out["position"] = result.positions
    path = "logs/last_backtest.csv"
    out.to_csv(path)
    print(f"资金曲线已保存: {path}")


if __name__ == "__main__":
    main()

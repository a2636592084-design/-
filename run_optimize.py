#!/usr/bin/env python3
"""参数优化 + 样本外验证 CLI。

用法：
    python run_optimize.py --market synthetic --strategy trend_stack --objective calmar
    python run_optimize.py --market crypto --symbol BTC/USDT --strategy supertrend --ppy 365

读法（这是重点）：
    - 看每一折的【样本外(OOS)】分数，而不是样本内。
    - 若 OOS 平均分远低于 IS 平均分 => 过拟合警告，这套参数别上实盘。
    - 目标默认卡玛(年化/回撤)，不是胜率——我们要的是"稳"，不是"看着爽"。
"""
from __future__ import annotations

import argparse

from qbot.backtest.optimize import PARAM_GRIDS, walk_forward
from qbot.data.loader import get_ohlcv
from qbot.strategies import REGISTRY


def main() -> None:
    p = argparse.ArgumentParser(description="qbot 参数优化 + 样本外验证")
    p.add_argument("--market", default="synthetic", choices=["synthetic", "crypto", "ashare"])
    p.add_argument("--symbol", default="DEMO")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--limit", type=int, default=1500)
    p.add_argument("--strategy", default="trend_stack", choices=list(PARAM_GRIDS))
    p.add_argument("--objective", default="calmar",
                   choices=["calmar", "sharpe", "sortino", "cagr"])
    p.add_argument("--splits", type=int, default=4)
    p.add_argument("--ppy", type=int, default=252)
    p.add_argument("--atr-stop", type=float, default=None,
                   help="叠加 ATR 移动止损，如 3.0")
    args = p.parse_args()

    df = get_ohlcv(args.market, args.symbol, args.timeframe, limit=args.limit)
    grid = PARAM_GRIDS[args.strategy]
    folds = walk_forward(df, REGISTRY[args.strategy], grid,
                         n_splits=args.splits, objective=args.objective,
                         ppy=args.ppy, atr_stop=args.atr_stop)

    print("=" * 72)
    print(f"  策略={args.strategy}  目标={args.objective}  标的={args.symbol}  "
          f"ATR止损={args.atr_stop}")
    print("=" * 72)
    print(f"{'折':<4}{'样本内(IS)':>12}{'样本外(OOS)':>14}   最优参数")
    print("-" * 72)
    is_avg = oos_avg = 0.0
    for f in folds:
        is_avg += f.is_score
        oos_avg += f.oos_score
        print(f"{f.fold:<4}{f.is_score:>12.2f}{f.oos_score:>14.2f}   {f.best_params}")
    n = max(len(folds), 1)
    is_avg /= n
    oos_avg /= n
    print("-" * 72)
    print(f"{'均值':<4}{is_avg:>12.2f}{oos_avg:>14.2f}")
    print("=" * 72)

    if oos_avg < 0:
        print("⚠️ 样本外为负：这套策略/参数在没见过的数据上是亏的，别上实盘。")
    elif oos_avg < is_avg * 0.5:
        print("⚠️ 样本外远低于样本内：明显过拟合迹象，谨慎对待，考虑简化参数。")
    else:
        print("✅ 样本内外相对一致：稳健性尚可，但仍需用小额实盘继续验证。")
    print("再次提醒：没有任何回测能保证未来盈利。这只是帮你避开最蠢的坑。")


if __name__ == "__main__":
    main()

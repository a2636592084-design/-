#!/usr/bin/env python3
"""盯盘推送：24h 循环扫描一篮子标的，信号变化时主动通知你。

这是"数据+信号"路线的盯盘核心——它不下单，只在某标的从空仓变买入
（或反过来）时提醒你，让你不用一直盯屏。

用法：
    # 干跑：只打印不真发，先看逻辑（无需配任何密钥）
    python run_watch.py --market crypto --symbols BTC/USDT ETH/USDT SOL/USDT --dry-run

    # 正式盯盘（配好 .env 的通知渠道后，每 5 分钟扫一次）
    python run_watch.py --market ashare --symbols 600519 000001 300750 --strategy regime_switch --poll 300

    # 一轮就退出（配合系统定时任务用）
    python run_watch.py --market crypto --symbols BTC/USDT --once
"""
from __future__ import annotations

import argparse
import time

from qbot.data.loader import get_ohlcv
from qbot.logger import get_logger
from qbot.notify import build_from_env, notify_all
from qbot.notify.tracker import LABEL, SignalTracker
from qbot.risk.stops import apply_atr_trailing_stop
from qbot.strategies import REGISTRY

log = get_logger("qbot.watch")


def scan_once(args, strat, tracker, notifiers) -> None:
    for sym in args.symbols:
        try:
            df = get_ohlcv(args.market, sym, args.timeframe, limit=400,
                           fallback_synthetic=(args.market == "synthetic"))
            pos = strat.generate_positions(df).fillna(0.0)
            if strat.long_only:
                pos = pos.clip(lower=0.0)
            if args.atr_stop:
                pos = apply_atr_trailing_stop(df, pos, args.atr_stop)
            target = float(pos.iloc[-1]) if len(pos) else 0.0
            price = float(df["close"].iloc[-1])

            key = f"{args.market}:{sym}:{args.strategy}"
            changed, old, new = tracker.update(key, target)
            log.info("%s 价=%.4f 信号=%s%s", sym, price, LABEL[new],
                     "  [变化!]" if changed else "")
            if changed:
                title = f"【信号变化】{sym} {LABEL[old]} → {LABEL[new]}"
                msg = (f"标的: {sym}\n策略: {args.strategy}\n最新价: {price}\n"
                       f"由 {LABEL[old]} 变为 {LABEL[new]}\n"
                       f"时间: {df.index[-1]}\n（提醒仅供参考，本工具不自动下单）")
                if args.dry_run:
                    log.info("[dry-run] 本应推送: %s", title)
                else:
                    notify_all(title, msg, notifiers)
        except Exception as e:  # noqa: BLE001
            log.warning("扫描 %s 失败: %s", sym, e)


def main() -> None:
    p = argparse.ArgumentParser(description="qbot 盯盘推送")
    p.add_argument("--market", default="crypto", choices=["crypto", "ashare", "synthetic"])
    p.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--strategy", default="regime_switch", choices=list(REGISTRY))
    p.add_argument("--atr-stop", type=float, default=None)
    p.add_argument("--poll", type=int, default=300, help="轮询间隔秒(默认5分钟)")
    p.add_argument("--once", action="store_true", help="扫一轮就退出")
    p.add_argument("--dry-run", action="store_true", help="只打印不真发通知")
    args = p.parse_args()

    strat = REGISTRY[args.strategy]()
    tracker = SignalTracker()
    notifiers = None if args.dry_run else build_from_env()

    log.info("盯盘启动: %s %s 策略=%s 周期=%ds dry_run=%s",
             args.market, args.symbols, args.strategy, args.poll, args.dry_run)
    if args.once:
        scan_once(args, strat, tracker, notifiers)
        return
    try:
        while True:
            scan_once(args, strat, tracker, notifiers)
            time.sleep(args.poll)
    except KeyboardInterrupt:
        log.info("收到停止信号，盯盘退出。")


if __name__ == "__main__":
    main()

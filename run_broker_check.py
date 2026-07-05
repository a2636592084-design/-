#!/usr/bin/env python3
"""OKX 下单链路自检（上真钱前的最后一道关）。

为什么必须先跑这个：合约下单代码我没法替你连 OKX 实测——张数换算、杠杆设置、
持仓读取、平仓，每一步都可能因为你账户的持仓模式/最小下单量而出问题。这个脚本
按顺序把每一步走一遍并打勾/打叉，让你（和我）确认链路真的通，再谈真钱。

默认【只读检查】：连接、余额、市场信息、资金费率、（合约）杠杆设置——不下任何单。
加 --place-test-order 才会下一个【极小测试单】并立刻平掉（务必先在【模拟盘】跑）。

用法：
    python run_broker_check.py                       # 只读自检（安全）
    python run_broker_check.py --trade-type swap     # 合约只读自检
    python run_broker_check.py --trade-type swap --place-test-order --symbol BTC/USDT:USDT
"""
from __future__ import annotations

import argparse
import sys
import time

from qbot.config import AppConfig

OK, BAD, WARN = "  ✅", "  ❌", "  ⚠️ "


def _p(tag, msg):
    print(f"{tag} {msg}")


def main() -> None:
    ap = argparse.ArgumentParser(description="OKX 下单链路自检")
    ap.add_argument("--trade-type", default="spot", choices=["spot", "swap"])
    ap.add_argument("--leverage", type=int, default=3)
    ap.add_argument("--symbol", default=None,
                    help="测试标的（现货如 BTC/USDT；合约如 BTC/USDT:USDT）")
    ap.add_argument("--place-test-order", action="store_true",
                    help="真的下一个极小测试单再平掉（先在模拟盘用！）")
    ap.add_argument("--notional", type=float, default=6.0,
                    help="测试单名义金额USDT(默认6，OKX合约常见最小约5)")
    args = ap.parse_args()

    print("=" * 56)
    print(" OKX 下单链路自检")
    print("=" * 56)

    cfg = AppConfig.load()
    creds = cfg.okx
    # 1) 密钥
    if not creds.configured:
        _p(BAD, "未配置 OKX 密钥：请在 .env 填 OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE")
        sys.exit(1)
    _p(OK, f"密钥已配置（demo={'是' if creds.demo else '否·真钱！'}）")
    if not creds.demo:
        _p(WARN, "当前是【实盘】(OKX_DEMO=0)！自检强烈建议先用模拟盘。")

    from qbot.broker.okx import OKXBroker
    try:
        broker = OKXBroker(creds.api_key, creds.secret, creds.passphrase, demo=creds.demo,
                           trade_type=args.trade_type, leverage=args.leverage)
    except Exception as e:  # noqa: BLE001
        _p(BAD, f"创建 broker 失败：{e}")
        sys.exit(1)

    # 2) 认证 + 余额
    try:
        acc = broker.get_account()
        _p(OK, f"连接+认证成功。可用 USDT ≈ {acc.cash:.2f}")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if any(k in msg for k in ("50113", "Invalid Sign", "401", "Unauthorized")):
            _p(BAD, "认证失败(50113/Invalid Sign)：key/secret/passphrase 有误，"
                    "或用了实盘key配模拟盘。请用【模拟交易】里生成的专用Key。")
        elif any(k in msg for k in ("getaddrinfo", "resolve", "Max retries", "ConnectionError")):
            _p(BAD, "连不上OKX：Clash没开系统代理，或 .env 没配 HTTPS_PROXY。")
        else:
            _p(BAD, f"读取账户失败：{msg[:120]}")
        sys.exit(1)

    sym = args.symbol or ("BTC/USDT:USDT" if args.trade_type == "swap" else "BTC/USDT")

    # 3) 市场信息 + 最小下单量
    try:
        broker.exchange.load_markets()
        m = broker.exchange.market(sym)
        csize = float(m.get("contractSize") or 1.0)
        min_amt = ((m.get("limits") or {}).get("amount") or {}).get("min")
        _p(OK, f"市场 {sym}：contractSize={csize} 最小下单量={min_amt}")
    except Exception as e:  # noqa: BLE001
        _p(BAD, f"读取市场 {sym} 失败：{str(e)[:120]}")
        sys.exit(1)

    price = broker.market_price(sym)
    _p(OK, f"最新价 {price}")

    # 4) 合约：杠杆 + 持仓模式 + 资金费率
    if args.trade_type == "swap":
        try:
            broker._ensure_leverage(sym)
            _p(OK, f"杠杆已设置为 {args.leverage}x（逐仓 isolated）")
        except Exception as e:  # noqa: BLE001
            _p(WARN, f"设置杠杆异常：{str(e)[:100]}（可能账户是双向持仓模式）")
        try:
            fr = broker.fetch_funding([sym]).get(sym, {})
            _p(OK, f"资金费率 {sym}：{fr.get('rate')}（下次结算 {fr.get('next_ts')}）")
        except Exception as e:  # noqa: BLE001
            _p(WARN, f"资金费率读取失败：{str(e)[:80]}")

    # 5) 下单自检（可选，谨慎）
    if not args.place_test_order:
        print("-" * 56)
        _p(OK, "只读自检通过。要验证真实下单，加 --place-test-order（先用模拟盘）。")
        return

    print("-" * 56)
    _p(WARN, f"即将下一个极小市价单（名义≈{args.notional}U）并立刻平掉：{sym}")
    from qbot.broker.base import Order
    amt = args.notional / price if price > 0 else 0.0
    try:
        f1 = broker.submit(Order(sym, "buy", amt))
        _p(OK, f"开仓成交：买入 {sym} @ {f1.price}（数量≈{amt:.6f}）")
    except Exception as e:  # noqa: BLE001
        _p(BAD, f"开仓失败：{str(e)[:140]}")
        _p(WARN, "常见原因：低于最小下单量→加大 --notional；账户双向持仓→改单向；保证金不足。")
        sys.exit(1)

    time.sleep(2)
    try:
        acc2 = broker.get_account()
        pos = acc2.positions.get(sym)
        if pos and abs(pos.amount) > 0:
            _p(OK, f"读回持仓：{sym} 数量={pos.amount:.6f} 均价={pos.avg_price} "
                   f"强平价={pos.liquidation_price or '—'}")
        else:
            _p(WARN, "未读到持仓（可能已被撮合成其它方向或延迟），继续尝试平仓。")
    except Exception as e:  # noqa: BLE001
        _p(WARN, f"读回持仓异常：{str(e)[:100]}")
        pos = None

    try:
        close_amt = abs(pos.amount) if pos and pos.amount else amt
        broker.submit(Order(sym, "sell", close_amt))
        _p(OK, f"平仓成交：卖出 {sym} 数量≈{close_amt:.6f}")
    except Exception as e:  # noqa: BLE001
        _p(BAD, f"平仓失败：{str(e)[:140]}。请去 OKX APP 手动确认/平掉该测试仓！")
        sys.exit(1)

    print("-" * 56)
    _p(OK, "🎉 完整下单链路自检通过：开仓→读持仓→平仓 全部成功。可以进入模拟盘长期跑测了。")


if __name__ == "__main__":
    main()

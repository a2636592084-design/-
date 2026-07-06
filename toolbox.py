#!/usr/bin/env python3
"""🧰 手动工具箱 —— 平时想手动做点什么，跑这一个文件、点数字就行。

    python toolbox.py            # 合约(swap)模式，跟你实盘一致（推荐）
    python toolbox.py --spot     # 现货模式

菜单里能做的事（都不改动引擎、可随时用）：
  1 连接自检      2 看当前持仓     3 看余额/净值    4 看大盘态
  5 手动扫一轮    6 单看某币详情   7 看交易战绩     8 看交易所止损单
  9 紧急全平(二次确认)            0 退出

⚠️ 只要 .env 里 OKX_DEMO=1 就是模拟盘，怎么点都不动真钱。
"""
from __future__ import annotations

import argparse

from qbot.config import AppConfig

TRADE_TYPE = "swap"     # 由 --spot 覆盖
BENCH = "BTC/USDT:USDT"  # 大盘基准（现货模式会改成 BTC/USDT）

OK, BAD, WARN, DOT = "✅", "❌", "⚠️ ", "·"


def _hr(title=""):
    print("─" * 60)
    if title:
        print(title)


def _broker():
    """按 .env 建一个 OKX broker（失败给人话提示，返回 None）。"""
    creds = AppConfig.load().okx
    if not creds.configured:
        print(f"{BAD} 未配置 OKX 密钥：请在 .env 填 OKX_API_KEY / OKX_API_SECRET / OKX_PASSPHRASE")
        return None
    from qbot.broker.okx import OKXBroker
    try:
        return OKXBroker(creds.api_key, creds.secret, creds.passphrase, demo=creds.demo,
                         trade_type=TRADE_TYPE, leverage=3, margin_mode="isolated")
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} 创建 broker 失败：{str(e)[:120]}")
        return None


def _friendly(e: Exception) -> str:
    m = str(e)
    if any(k in m for k in ("50113", "Invalid Sign", "401", "Unauthorized")):
        return "OKX认证失败：确认 .env 三项密钥正确、且是【模拟交易】里生成的专用Key。"
    if any(k in m for k in ("getaddrinfo", "resolve", "Max retries", "ConnectionError")):
        return "连不上OKX：多半是 Clash 没开系统代理，或 .env 没配 HTTPS_PROXY。"
    return m[:140]


# ───────────────────────── 1 连接自检 ─────────────────────────
def action_selfcheck():
    _hr("🩺 OKX 连接自检（只读，不下单）")
    creds = AppConfig.load().okx
    if not creds.configured:
        print(f"{BAD} 未配置 OKX 密钥（.env 里填三项）。")
        return
    print(f"{OK} 密钥已配置（demo={'是·模拟盘' if creds.demo else '否·真钱！'}）")
    if not creds.demo:
        print(f"{WARN} 现在是【实盘】(OKX_DEMO=0)！确认你真的想动真钱。")
    b = _broker()
    if not b:
        return
    try:
        acc = b.get_account()
        print(f"{OK} 连接+认证成功。可用 USDT ≈ {acc.cash:.2f}")
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {_friendly(e)}")
        return
    if TRADE_TYPE == "swap":
        pm = b.position_mode()
        if pm == "net_mode":
            print(f"{OK} 持仓模式：单向(net) ✓ 与本系统匹配")
        elif pm == "long_short_mode":
            print(f"{BAD} 持仓模式：双向/对冲！本系统按单向交易，下单会报 51000。"
                  "请去 OKX 交易设置改成【单向持仓】。")
        else:
            print(f"{WARN} 未能读取持仓模式（若下单报 posSide 错，改成单向持仓）。")
    print(f"{OK} 自检完成。要完整下单链路自检，跑：python run_broker_check.py --trade-type {TRADE_TYPE}")


# ───────────────────────── 2 看当前持仓 ─────────────────────────
def action_positions():
    _hr("📊 当前持仓（交易所实时口径，与 OKX App 一致）")
    b = _broker()
    if not b:
        return
    try:
        acc = b.get_account()
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {_friendly(e)}")
        return
    held = [(s, p) for s, p in acc.positions.items() if abs(p.amount) > 1e-12]
    if not held:
        print(f"{DOT} 当前无持仓（空仓）。")
        return
    funding = {}
    if TRADE_TYPE == "swap":
        try:
            funding = b.fetch_funding([s for s, _ in held])
        except Exception:  # noqa: BLE001
            pass
    for s, p in held:
        side = "🟢多" if p.amount > 0 else "🔴空"
        px = p.mark_price or p.avg_price
        value = abs(p.amount) * px
        pnl = p.unrealized_pnl or (p.amount * (px - p.avg_price))
        pct = p.pnl_pct_exch or ((px / p.avg_price - 1) * 100 if p.avg_price else 0)
        print(f"\n{side} {s}")
        print(f"   数量 {abs(p.amount):.6f}  成本 {p.avg_price:.6f}  标记价 {px:.6f}")
        print(f"   持仓金额 ≈ {value:,.2f}U   浮动盈亏 {pnl:+.2f}U ({pct:+.2f}%)")
        if p.liquidation_price:
            dist = abs(px - p.liquidation_price) / px * 100 if px else 0
            flag = "  ⚠️ 离强平很近！" if dist < 15 else ""
            print(f"   强平价 {p.liquidation_price:.6f}（距现价 {dist:.1f}%）{flag}")
        fr = funding.get(s, {})
        if fr.get("rate") is not None:
            rate = fr["rate"] or 0
            paying = (p.amount > 0 and rate > 0) or (p.amount < 0 and rate < 0)
            print(f"   资金费率 {rate*100:+.4f}%  下次结算 {fr.get('next_ts')}"
                  f"  {'(你付费)' if paying else '(你收费)'}")


# ───────────────────────── 3 看余额/净值 ─────────────────────────
def action_balance():
    _hr("💰 账户余额与净值")
    b = _broker()
    if not b:
        return
    try:
        acc = b.get_account()
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {_friendly(e)}")
        return
    held = [(s, p) for s, p in acc.positions.items() if abs(p.amount) > 1e-12]
    prices = {s: (p.mark_price or p.avg_price) for s, p in held}
    equity = acc.equity(prices)
    print(f"{DOT} 可用 USDT ≈ {acc.cash:,.2f}")
    print(f"{DOT} 账户净值 ≈ {equity:,.2f}  （持仓 {len(held)} 个）")
    upnl = sum((p.unrealized_pnl or 0) for _, p in held)
    if held:
        print(f"{DOT} 持仓合计浮动盈亏 {upnl:+.2f}U")


# ───────────────────────── 4 看大盘态 ─────────────────────────
def action_regime():
    _hr("🧭 大盘方向态（决定策略要不要开新仓）")
    from qbot.data.loader import get_ohlcv
    from qbot.strategies.regime import regime_now, regime_series
    from qbot.strategies.indicators import adx as _adx, ema as _ema
    demo = AppConfig.load().okx.demo
    try:
        df = get_ohlcv("crypto", BENCH, "4h", limit=300, demo=demo, fallback_synthetic=False)
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} 拉 BTC 数据失败：{_friendly(e)}")
        return
    reg = regime_now(df)
    label = {1: "🟢 多头行情 → 只做多", -1: "🔴 空头行情 → 只做空",
             0: "🟡 震荡 → 暂停开新仓（策略在休息，正常）"}.get(reg, "—")
    adx_v = float(_adx(df)[0].iloc[-1])
    ema_v = float(_ema(df["close"], 200).iloc[-1])
    px = float(df["close"].iloc[-1])
    print(f"{DOT} 当前大盘态：{label}")
    print(f"{DOT} BTC 现价 {px:.1f}  EMA200 {ema_v:.1f}（{'价在上方=偏多' if px>ema_v else '价在下方=偏空'}）")
    print(f"{DOT} BTC ADX {adx_v:.1f}（<20=震荡无趋势，闸会暂停开仓）")


# ───────────────────────── 5 手动扫一轮 ─────────────────────────
def _build_scan_engine():
    from qbot.engine.build import build_engine
    eng, _info = build_engine({
        "market": "crypto", "broker": "paper", "trade_type": TRADE_TYPE,
        "strategy": "confluence", "allow_short": True, "quality": True,
        "market_gate": True, "top": 40, "timeframe": "4h",
    })
    return eng


def action_scan():
    _hr("🔍 手动扫一轮全市场（约 20–40 秒，不下单）")
    print(f"{DOT} 正在拉取标的与历史、计算共振分…")
    try:
        eng = _build_scan_engine()
        eng._regime = eng._market_regime()
        rows = eng._scan()
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {_friendly(e)}")
        return
    if not rows:
        print(f"{BAD} 没扫到有效标的（网络/代理？）")
        return
    reg = eng._regime
    reg_txt = {1: "多头(只做多)", -1: "空头(只做空)", 0: "震荡(暂停开仓)"}.get(reg, "—")
    print(f"{DOT} 大盘态：{reg_txt}   扫描 {len(rows)} 个标的\n")
    sig = [r for r in rows if r["long"] or r["short"]]
    sig.sort(key=lambda r: abs(r["strength"]), reverse=True)
    if not sig:
        print(f"{DOT} 当前没有任何做多/做空信号（都在观望）。")
        return
    print(f"{'标的':<18}{'方向':<6}{'共振分':>8}   顺不顺大盘")
    for r in sig[:20]:
        d = "🟢多" if r["long"] else "🔴空"
        want = 1 if r["long"] else -1
        gated = (reg not in (None,) and reg != 0 and want != reg) or reg == 0
        tag = "✋被闸拦" if gated else "✓会开仓" if reg not in (None,) else ""
        print(f"{r['symbol']:<18}{d:<6}{r['strength']:>+8.3f}   {tag}")
    print(f"\n{DOT} “被闸拦”=信号和大盘方向不符/大盘在震荡，引擎这轮不会开它。")


# ───────────────────────── 6 单看某币详情 ─────────────────────────
def action_detail():
    _hr("🔬 单看某个币的共振投票明细")
    q = input("输入代码（合约如 BTC/USDT:USDT，现货如 BTC/USDT，直接回车=BTC）：").strip()
    sym = q or BENCH
    from qbot.data.loader import get_ohlcv
    from qbot.strategies import REGISTRY
    demo = AppConfig.load().okx.demo
    try:
        df = get_ohlcv("crypto", sym, "4h", limit=300, demo=demo, fallback_synthetic=False)
        strat = REGISTRY["confluence"](allow_short=True)
        info = strat.explain(df)
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {_friendly(e)}")
        return
    print(f"\n{DOT} {sym}  现价 {float(df['close'].iloc[-1]):.6f}")
    print(f"{DOT} 结论：{info['conclusion']}   共振分 {info['score']}   ADX {info['adx']}")
    print(f"{DOT} 各指标投票（+1看多 / -1看空 / 0中性）：")
    for k, v in info["votes"].items():
        bar = "🟢" if v > 0 else "🔴" if v < 0 else "⚪"
        print(f"     {bar} {k:<12} {v:+d}")


# ───────────────────────── 7 看交易战绩 ─────────────────────────
def action_journal():
    _hr("📒 交易战绩（真实成交统计，来自交易日志）")
    q = input("看哪个盘的日志？(1=模拟量化 paper / 2=实盘加密 crypto，回车=crypto)：").strip()
    mode = "paper" if q == "1" else "crypto"
    from qbot.journal import TradeJournal
    st = TradeJournal(mode).stats()
    if not st["closed_trades"]:
        print(f"{DOT} [{mode}] 还没有已平仓的交易记录。")
        return
    print(f"{DOT} [{mode}] 已平仓 {st['closed_trades']} 笔  "
          f"胜 {st['wins']} / 负 {st['losses']}  胜率 {st['win_rate']}%")
    print(f"{DOT} 已实现盈亏 {st['realized_pnl']:+.2f}   盈亏比 {st['profit_factor'] or '—'}")
    print(f"{DOT} 均盈 {st['avg_win']:+.2f}  均亏 {st['avg_loss']:+.2f}  最大回撤 {st['max_drawdown']}%")
    rec = st.get("recent_closed") or []
    if rec:
        print(f"\n{DOT} 最近平仓：")
        for c in rec[:10]:
            print(f"     {c.get('ts','')}  {c.get('symbol','')}  盈亏 {c.get('pnl',0):+.2f}")


# ───────────────────────── 8 看交易所止损单 ─────────────────────────
def action_stops():
    _hr("🛑 交易所上挂着的止损单（计划委托）")
    if TRADE_TYPE != "swap":
        print(f"{DOT} 现货模式没有条件止损单。")
        return
    b = _broker()
    if not b:
        return
    try:
        acc = b.get_account()
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {_friendly(e)}")
        return
    held = [s for s, p in acc.positions.items() if abs(p.amount) > 1e-12]
    if not held:
        print(f"{DOT} 当前无持仓，通常也就没有止损单。")
    found = False
    for sym in held or [BENCH]:
        for params in ({"stop": True}, {"ordType": "conditional"}, {"trigger": True}):
            try:
                orders = b.exchange.fetch_open_orders(sym, params=params)
            except Exception:  # noqa: BLE001
                continue
            if orders:
                found = True
                for o in orders:
                    trig = o.get("triggerPrice") or o.get("stopPrice") or \
                        (o.get("info") or {}).get("slTriggerPx") or "—"
                    print(f"   {sym}  {o.get('side')}  触发价 {trig}  id={o.get('id')}")
                break
    if not found:
        print(f"{WARN} 没读到条件单（可能你的 ccxt 版本口径不同）。"
              "最稳的办法：去 OKX App → 委托 → 计划委托 里看。")


# ───────────────────────── 9 紧急全平 ─────────────────────────
def action_close_all():
    _hr("🧯 紧急平掉全部持仓（市价）")
    b = _broker()
    if not b:
        return
    try:
        acc = b.get_account()
    except Exception as e:  # noqa: BLE001
        print(f"{BAD} {_friendly(e)}")
        return
    held = [(s, p) for s, p in acc.positions.items() if abs(p.amount) > 1e-12]
    if not held:
        print(f"{DOT} 当前无持仓，无需平仓。")
        return
    print(f"{WARN} 将市价平掉以下 {len(held)} 个持仓：")
    for s, p in held:
        print(f"     {'多' if p.amount>0 else '空'} {s}  数量 {abs(p.amount):.6f}")
    demo = AppConfig.load().okx.demo
    tip = "（模拟盘）" if demo else "‼️ 这是真钱账户 ‼️"
    if input(f"\n确认全部市价平仓？{tip} 输入大写 YES 执行：").strip() != "YES":
        print(f"{DOT} 已取消，未做任何操作。")
        return
    from qbot.broker.base import Order
    for s, p in held:
        side = "sell" if p.amount > 0 else "buy"
        try:
            b.submit(Order(s, side, abs(p.amount)))
            print(f"{OK} 已平 {s}")
        except Exception as e:  # noqa: BLE001
            print(f"{BAD} 平 {s} 失败：{str(e)[:100]}（请去 OKX App 手动平）")
    print(f"{DOT} 平仓指令发送完毕。建议再点【2 看当前持仓】确认已清空。")


MENU = {
    "1": ("🩺 连接自检（密钥/连接/持仓模式）", action_selfcheck),
    "2": ("📊 看当前持仓（盈亏/强平价/资金费）", action_positions),
    "3": ("💰 看余额与净值", action_balance),
    "4": ("🧭 看大盘态（现在能不能开、开多还是空）", action_regime),
    "5": ("🔍 手动扫一轮全市场信号", action_scan),
    "6": ("🔬 单看某个币的共振投票明细", action_detail),
    "7": ("📒 看交易战绩（胜率/盈亏比/最近平仓）", action_journal),
    "8": ("🛑 看交易所挂着的止损单", action_stops),
    "9": ("🧯 紧急平掉全部持仓（二次确认）", action_close_all),
}


def main():
    global TRADE_TYPE, BENCH
    ap = argparse.ArgumentParser(description="手动工具箱")
    ap.add_argument("--spot", action="store_true", help="现货模式（默认合约swap，与你实盘一致）")
    args = ap.parse_args()
    if args.spot:
        TRADE_TYPE, BENCH = "spot", "BTC/USDT"

    demo = AppConfig.load().okx.demo
    while True:
        print("\n" + "═" * 60)
        print(f" 🧰 手动工具箱   [{'合约swap' if TRADE_TYPE=='swap' else '现货spot'}]   "
              f"{'模拟盘(安全)' if demo else '⚠️实盘·真钱'}")
        print("═" * 60)
        for k, (label, _) in MENU.items():
            print(f"  {k}. {label}")
        print("  0. 退出")
        choice = input("\n输入数字回车 > ").strip()
        if choice in ("0", "q", "exit", ""):
            print("再见 👋")
            return
        item = MENU.get(choice)
        if not item:
            print(f"{WARN} 没有这个选项，输 0-9。")
            continue
        try:
            item[1]()
        except KeyboardInterrupt:
            print("\n（已中断该操作，回到菜单）")
        except Exception as e:  # noqa: BLE001
            print(f"{BAD} 操作出错：{str(e)[:150]}")
        input("\n回车返回菜单…")


if __name__ == "__main__":
    main()

"""全市场标的池：列出可扫描的标的，并按成交量取 Top-N。

诚实边界：扫描可以"全市场"，但持仓必须"有限个"（见 engine/portfolio.py）。
每轮扫数百标的走 HTTP 较慢，故按成交额取 Top-N（可配）；被截断多少一定 log。
"""
from __future__ import annotations

import os

from .logger import get_logger

log = get_logger("qbot.universe")

# A股无 akshare 时的内置主流清单（沪深各行业龙头，覆盖常见标的）
_ASHARE_FALLBACK = [
    "600519", "601318", "600036", "600900", "601166", "000858", "600030",
    "000333", "600276", "601888", "000651", "002594", "300750", "600887",
    "601012", "000001", "600000", "601398", "601288", "601988", "600028",
    "601857", "600585", "000002", "600048", "601668", "600031", "000725",
    "002415", "002714", "300760", "600690", "601899", "603288", "000568",
    "002304", "600809", "603259", "300059", "002460", "300124", "601899",
    "600104", "601633", "002027", "600809", "601211", "601688", "600570",
]


# 优质币白名单，按【档位】划分（可按需增删，告诉我名字即可）：
#   一线(主流大盘)：市值最大、流动性最深、相对波动最小——趋势策略最吃得动、止损最靠谱。
#   二线(蓝筹)：有真实业务/长历史、流动性仍充足，但排名在主流之下。
#   三线(小盘/高波动)：更小更颠、带杠杆容易插穿止损——【默认不交易】，仅留存供参考/可选放开。
# 默认可交易 = 一线 + 二线（三线与妖币/纯meme/次新一律排除）。

# 一线·主流大盘（约15个）
TIER1_CRYPTO = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "TRX", "LINK",
    "DOT", "BCH", "LTC", "TON", "XLM",
}
# 二线·蓝筹（老牌 L1/L2 + 老牌 DeFi + 基础设施里流动性好、够成熟的）
TIER2_CRYPTO = {
    "UNI", "AAVE", "ATOM", "NEAR", "APT", "ARB", "OP", "SUI", "ICP",
    "FIL", "INJ", "LDO", "MKR", "ETC", "HBAR", "RENDER", "IMX", "GRT", "ALGO",
}
# 三线·小盘/高波动（默认不交易；想放开就并进 QUALITY_CRYPTO 或用 --all-coins）
TIER3_CRYPTO = {
    "EGLD", "EOS", "XTZ", "FLOW", "KSM", "STX", "MINA", "CFX",
    "CRV", "SNX", "COMP", "DYDX", "SUSHI", "1INCH", "YFI", "RUNE",
    "THETA", "QNT", "KAVA", "ENS", "WOO", "ZEC", "DASH", "LPT",
    "SAND", "MANA", "AXS", "GALA", "ENJ", "CHZ", "APE",
}

# 实际用于过滤的白名单：一线 + 二线（不含三线）
QUALITY_CRYPTO = TIER1_CRYPTO | TIER2_CRYPTO


def crypto_universe(types=("spot",), quote: str = "USDT", top: int = 120,
                    quality: bool = False) -> list[str]:
    """OKX 全市场加密标的，按 24h 成交额降序取 Top-N。
    types: 可含 'spot'(现货) / 'swap'(永续合约) / 'future'(交割) 等 ccxt 类型。
    quality=True：只保留优质币白名单（一线主流+二线蓝筹，排除三线小盘/妖币）。"""
    try:
        import ccxt
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 ccxt，请先 pip install ccxt") from e

    ex = ccxt.okx({"enableRateLimit": True})
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        ex.proxies = {"http": proxy, "https": proxy}
    ca = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
    if os.path.exists(ca):
        try:
            ex.session.verify = ca
        except Exception:  # noqa: BLE001
            pass

    markets = ex.load_markets()
    cands = [m for m in markets.values()
             if m.get("active") and m.get("type") in types and m.get("quote") == quote]
    if quality:                                    # 只留优质币白名单
        before = len(cands)
        cands = [m for m in cands if str(m.get("base", "")).upper() in QUALITY_CRYPTO]
        log.info("优质币过滤(一线+二线)：%d → %d（排除三线小盘/妖币/次新）", before, len(cands))
    # 用 ticker 的成交额排序（一次性拉全部 ticker）
    try:
        tickers = ex.fetch_tickers([m["symbol"] for m in cands])
    except Exception as e:  # noqa: BLE001
        log.warning("拉 ticker 失败(%s)，按字母序返回。", e)
        tickers = {}

    def vol(sym: str) -> float:
        t = tickers.get(sym) or {}
        return float(t.get("quoteVolume") or 0.0)

    syms = sorted((m["symbol"] for m in cands), key=vol, reverse=True)
    total = len(syms)
    picked = syms[:top]
    log.info("加密全市场 %s：共 %d 个可交易，取成交额 Top %d（截断 %d 个）",
             "/".join(types), total, len(picked), max(0, total - len(picked)))
    return picked


def ashare_universe(top: int = 100) -> list[str]:
    """A股全市场代码。有 akshare 用全量，否则用内置主流清单。"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        codes = [str(c).zfill(6) for c in df["code"].tolist()]
        total = len(codes)
        picked = codes[:top]
        log.info("A股全市场：共 %d 只，取前 %d（截断 %d）",
                 total, len(picked), max(0, total - len(picked)))
        return picked
    except Exception as e:  # noqa: BLE001
        log.warning("akshare 不可用(%s)，用内置主流A股清单(%d只)。",
                    e, len(_ASHARE_FALLBACK))
        # 去重保序
        seen, out = set(), []
        for c in _ASHARE_FALLBACK:
            if c not in seen:
                seen.add(c)
                out.append(c)
        return out[:top]


def get_universe(market: str, top: int = 120, types=("spot",), quote: str = "USDT",
                 quality: bool = False):
    market = market.lower()
    if market == "crypto":
        return crypto_universe(types=types, quote=quote, top=top, quality=quality)
    if market == "ashare":
        return ashare_universe(top=top)
    if market == "synthetic":
        return [f"DEMO{i}" for i in range(1, min(top, 12) + 1)]
    raise ValueError(f"未知市场: {market}")

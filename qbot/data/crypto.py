"""加密货币行情：通过 ccxt 访问 OKX。

ccxt 用一套接口覆盖上百家交易所，OKX 只是其中之一。
拉不到数据时抛出清晰的异常，由上层决定是否回退到合成数据。
"""
from __future__ import annotations

import pandas as pd

from ..logger import get_logger

log = get_logger("qbot.data.crypto")


def fetch_okx_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "1d",
    limit: int = 1000,
    demo: bool = True,
) -> pd.DataFrame:
    try:
        import ccxt  # 延迟导入，未安装也不影响其它功能
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 ccxt，请先 `pip install ccxt`") from e

    exchange = ccxt.okx({"enableRateLimit": True})
    if demo:
        # OKX 模拟盘：请求头带 x-simulated-trading
        exchange.headers = {"x-simulated-trading": "1"}

    log.info("从 OKX 拉取 %s %s (limit=%d)", symbol, timeframe, limit)
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.set_index("ts").sort_index()


def list_okx_symbols(quote: str = "USDT") -> list[str]:
    """列出 OKX 上所有以某计价货币结算的现货标的（用于"全市场"扫描）。"""
    import ccxt

    exchange = ccxt.okx({"enableRateLimit": True})
    markets = exchange.load_markets()
    return sorted(
        s for s, m in markets.items()
        if m.get("spot") and m.get("quote") == quote and m.get("active")
    )

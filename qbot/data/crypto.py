"""加密货币行情：通过 ccxt 访问 OKX。

ccxt 用一套接口覆盖上百家交易所，OKX 只是其中之一。
拉不到数据时抛出清晰的异常，由上层决定是否回退到合成数据。
"""
from __future__ import annotations

import os

import pandas as pd

from ..logger import get_logger

log = get_logger("qbot.data.crypto")


def _apply_proxy(exchange) -> None:
    """若运行环境配置了 HTTPS 代理（如受管沙箱），让 ccxt 走代理。
    普通个人电脑没有这些环境变量，本函数不产生任何影响。"""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        exchange.proxies = {"http": proxy, "https": proxy}
    ca = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
    if os.path.exists(ca):
        try:
            exchange.session.verify = ca
        except Exception:  # noqa: BLE001
            pass


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
    _apply_proxy(exchange)
    # 注意：行情/K线是公开市场数据，永远走真实市场端点。
    # 绝不带 x-simulated-trading 头——OKX 模拟盘的历史K线是假数据(会污染回测)。
    # demo 只影响下单路由(见 broker/okx.py)，与拉行情无关。故此处忽略 demo 参数。

    log.info("从 OKX 拉取 %s %s (目标 %d 根)", symbol, timeframe, limit)
    # OKX 单次上限约 300 根，超过则向历史分页回溯拼接
    rows: list = []
    batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=min(limit, 300))
    rows.extend(batch)
    guard = 0
    while len(rows) < limit and batch and guard < 30:
        guard += 1
        oldest = rows[0][0]
        # OKX: after=返回早于该时间戳的记录（向历史回溯）
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe,
                                     limit=100, params={"after": oldest})
        batch = [r for r in batch if r[0] < oldest]
        if not batch:
            break
        rows = batch + rows

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.drop_duplicates(subset="ts").set_index("ts").sort_index()
    return df.tail(limit)


def fetch_okx_ohlcv_before(
    symbol: str = "BTC/USDT",
    timeframe: str = "1d",
    before_ms: int = 0,
    limit: int = 300,
) -> pd.DataFrame:
    """拉取早于 before_ms 时间戳的一页历史K线（供图表向左滚动加载更多）。"""
    import ccxt

    exchange = ccxt.okx({"enableRateLimit": True})
    _apply_proxy(exchange)
    # OKX: after=返回早于该时间戳的记录（向历史回溯）
    params = {"after": int(before_ms)} if before_ms else {}
    rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe,
                                limit=min(limit, 300), params=params)
    if before_ms:
        rows = [r for r in rows if r[0] < before_ms]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df.drop_duplicates(subset="ts").set_index("ts").sort_index()


def list_okx_symbols(quote: str = "USDT") -> list[str]:
    """列出 OKX 上所有以某计价货币结算的现货标的（用于"全市场"扫描）。"""
    import ccxt

    exchange = ccxt.okx({"enableRateLimit": True})
    _apply_proxy(exchange)
    markets = exchange.load_markets()
    return sorted(
        s for s, m in markets.items()
        if m.get("spot") and m.get("quote") == quote and m.get("active")
    )

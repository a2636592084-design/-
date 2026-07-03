"""统一数据入口：屏蔽市场差异，拉不到就优雅回退到合成数据。"""
from __future__ import annotations

import pandas as pd

from ..logger import get_logger
from .synthetic import synthetic_ohlcv

log = get_logger("qbot.data.loader")


def get_ohlcv(
    market: str,
    symbol: str,
    timeframe: str = "1d",
    limit: int = 1000,
    fallback_synthetic: bool = True,
    demo: bool = True,
) -> pd.DataFrame:
    """market: 'crypto' | 'ashare' | 'synthetic'。

    出错时若 fallback_synthetic=True，则返回合成数据并打印告警，
    保证上层流程（回测/面板）永远有数据可用，不会因网络问题崩掉。
    """
    market = market.lower()
    try:
        if market == "crypto":
            from .crypto import fetch_okx_ohlcv
            return fetch_okx_ohlcv(symbol, timeframe, limit, demo=demo)
        if market == "ashare":
            from .ashare import fetch_ashare_daily
            return fetch_ashare_daily(symbol)
        if market == "synthetic":
            return synthetic_ohlcv(periods=limit)
        raise ValueError(f"未知市场: {market}")
    except Exception as e:  # noqa: BLE001
        if fallback_synthetic:
            log.warning("拉取 %s/%s 失败(%s)，回退合成数据。", market, symbol, e)
            return synthetic_ohlcv(periods=limit)
        raise

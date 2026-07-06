"""统一数据入口：屏蔽市场差异，拉不到就优雅回退到合成数据。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..logger import get_logger
from .synthetic import synthetic_ohlcv

log = get_logger("qbot.data.loader")


def sanitize_ohlcv(df: pd.DataFrame, name: str = "") -> pd.DataFrame:
    """清洗脏 K 线：剔除价格非正、high<low、或价格离本地中位数离谱的坏点。

    防御任何数据源偶发的错误行情（如交易所返回的异常插针/错误刻度），
    否则一根 close=1390 的坏 BTC K 线就能把整条回测资金曲线打成 -99%。
    用【居中滚动中位数】判定离群，仅作数据预处理（非交易信号，可用未来点）。
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    cols = ["open", "high", "low", "close"]
    med = df["close"].rolling(7, center=True, min_periods=1).median()

    positive = (df[cols] > 0).all(axis=1)
    hl_ok = df["high"] >= df["low"]
    # 影线离中位数过远 / 收盘离中位数过远，判为坏点（阈值取宽，只杀明显垃圾）
    wick_ok = (df["high"] <= med * 2.5) & (df["low"] >= med * 0.4)
    close_ok = (df["close"] / med - 1.0).abs() <= 0.6

    good = positive & hl_ok & wick_ok & close_ok
    dropped = int((~good).sum())
    if dropped:
        log.warning("清洗 %s：剔除 %d 根脏K线（共 %d 根）", name or "数据", dropped, len(df))
    return df[good]


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
            return sanitize_ohlcv(
                fetch_okx_ohlcv(symbol, timeframe, limit, demo=demo), f"{market}/{symbol}")
        if market == "ashare":
            from .ashare import fetch_ashare_daily
            return sanitize_ohlcv(fetch_ashare_daily(symbol), f"{market}/{symbol}")
        if market in ("us", "hk", "forex", "futures"):
            from .yahoo import fetch_yahoo_ohlcv
            return sanitize_ohlcv(
                fetch_yahoo_ohlcv(symbol, timeframe, limit), f"{market}/{symbol}")
        if market == "synthetic":
            return synthetic_ohlcv(periods=limit)
        raise ValueError(f"未知市场: {market}")
    except Exception as e:  # noqa: BLE001
        if fallback_synthetic:
            log.warning("拉取 %s/%s 失败(%s)，回退合成数据。", market, symbol, e)
            return synthetic_ohlcv(periods=limit)
        raise

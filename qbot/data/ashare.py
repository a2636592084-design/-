"""A股行情：通过 akshare（免费、无需 token）。

akshare 背后聚合了东财/新浪等公开数据，适合做研究与回测。
注意：akshare 是"数据"，不是"下单通道"。A股自动下单见 broker/ 说明。
"""
from __future__ import annotations

import pandas as pd

from ..logger import get_logger

log = get_logger("qbot.data.ashare")

_RENAME = {
    "日期": "date", "开盘": "open", "最高": "high",
    "最低": "low", "收盘": "close", "成交量": "volume",
}


def fetch_ashare_daily(
    symbol: str = "600519",
    start: str = "20200101",
    end: str = "20301231",
    adjust: str = "qfq",  # 前复权，回测默认用它避免除权跳空
) -> pd.DataFrame:
    try:
        import akshare as ak
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("未安装 akshare，请先 `pip install akshare`") from e

    log.info("拉取 A股 %s [%s~%s] adjust=%s", symbol, start, end, adjust)
    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily",
        start_date=start, end_date=end, adjust=adjust,
    )
    df = df.rename(columns=_RENAME)
    df["date"] = pd.to_datetime(df["date"])
    cols = ["open", "high", "low", "close", "volume"]
    return df.set_index("date")[cols].sort_index()


def list_ashare_symbols() -> pd.DataFrame:
    """全部 A股代码+名称，用于全市场扫描/选股。"""
    import akshare as ak

    return ak.stock_info_a_code_name()

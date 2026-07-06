"""A股行情。

主数据源：腾讯财经历史K线（免费、无需 token、无重依赖，境内外都稳）。
备用数据源：akshare（若已安装且网络可达）。
注意：这里是"数据"，不是"下单通道"。A股自动下单见 broker/ 说明与 README。
"""
from __future__ import annotations

import json
import os

import pandas as pd

from ..logger import get_logger

log = get_logger("qbot.data.ashare")


def _market_prefix(code: str) -> str:
    """按代码判断交易所前缀：sh(沪) / sz(深) / bj(北)。"""
    code = code.strip()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if code.startswith(("5", "6", "9", "688")):     # 沪市A股/科创/基金
        return "sh" + code
    if code.startswith(("4", "8")):                 # 北交所
        return "bj" + code
    return "sz" + code                              # 深市A股/创业板 0/3/2


def _http_get(url: str, timeout: int = 25) -> str:
    """带代理/CA 兼容的 GET。受管沙箱有 HTTPS_PROXY 时自动走代理，本地则直连。"""
    import requests

    ca = os.environ.get("SSL_CERT_FILE")
    if not ca or not os.path.exists(ca):
        ca = "/root/.ccr/ca-bundle.crt"
    verify = ca if os.path.exists(ca) else True
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com"}
    r = requests.get(url, timeout=timeout, headers=headers, verify=verify)
    r.raise_for_status()
    return r.text


def fetch_ashare_daily(
    symbol: str = "600519",
    start: str = "20200101",
    end: str = "20301231",
    adjust: str = "qfq",   # 前复权，回测默认，避免除权跳空
    limit: int = 800,
) -> pd.DataFrame:
    prefix = _market_prefix(symbol)
    s = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    e = f"{end[:4]}-{end[4:6]}-{end[6:]}"
    kind = {"qfq": "qfqday", "hfq": "hfqday", "": "day", None: "day"}.get(adjust, "qfqday")
    fq = adjust if adjust in ("qfq", "hfq") else ""
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={prefix},day,{s},{e},{limit},{fq}")
    log.info("拉取 A股 %s [%s~%s] adjust=%s (腾讯)", symbol, start, end, adjust)

    raw = json.loads(_http_get(url))
    node = raw["data"][prefix]
    rows = node.get(kind) or node.get("day") or node.get("qfqday")
    if not rows:
        raise RuntimeError(f"腾讯未返回 {symbol} 的K线数据")

    # 腾讯字段顺序：[日期, 开, 收, 高, 低, 成交量(手)]
    df = pd.DataFrame([r[:6] for r in rows],
                      columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open", "close", "high", "low", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    cols = ["open", "high", "low", "close", "volume"]
    return df.set_index("date")[cols].sort_index()


def list_ashare_symbols() -> pd.DataFrame:
    """全部 A股代码+名称（需要 akshare；用于全市场扫描/选股）。"""
    try:
        import akshare as ak
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("列出全A股需要 akshare：pip install akshare") from e
    return ak.stock_info_a_code_name()

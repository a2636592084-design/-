"""多市场行情：美股 / 港股 / 外汇 / 期货，统一走 Yahoo Finance 公开 chart 接口。

为什么用 Yahoo：一个免费、无需 key 的源就能覆盖这四类市场，符合"一个源覆盖多市场"
的思路（就像 A股用腾讯、加密用 OKX）。境内需要走代理（Clash），本模块复用项目现成的
HTTPS_PROXY / CA 兼容写法。

诚实边界：
- Yahoo 原生周期只有 1m/5m/15m/30m/60m/1d/1wk/1mo；2h/4h/6h/12h 由 60m 数据**重采样**得到。
- 分钟级历史有窗口限制（1m 约 7 天、5~30m 约 60 天、60m 约 2 年），日线及以上可拉多年。
- 标的"搜索"用的是**精选常见清单**（各市场龙头/主力合约），不是全球全量列表——
  免费源没有"列出全部"的稳定接口，精选清单已覆盖绝大多数人要看的品种。
"""
from __future__ import annotations

import json
import os
import time

import pandas as pd

from ..logger import get_logger

log = get_logger("qbot.data.yahoo")

# 我们的周期 -> Yahoo 原生 interval；不在此表的(2h/4h/6h/12h)由 60m 重采样
_IV = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
       "1h": "60m", "1d": "1d", "1w": "1wk", "1M": "1mo"}
# 各 interval 的回溯窗口（秒），受 Yahoo 分钟级历史限制
_WINDOW = {"1m": 6, "5m": 55, "15m": 55, "30m": 55, "60m": 700,
           "1d": 1900, "1wk": 3650, "1mo": 7300}   # 单位：天
# 需要重采样的周期 -> pandas 规则
_RESAMPLE = {"2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h"}


def _http_get(url: str, timeout: int = 25) -> str:
    """带代理/CA 兼容的 GET（同 ashare 写法）。Yahoo 需要浏览器 UA，否则 403。"""
    import requests

    ca = os.environ.get("SSL_CERT_FILE")
    if not ca or not os.path.exists(ca):
        ca = "/root/.ccr/ca-bundle.crt"
    verify = ca if os.path.exists(ca) else True
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
    r = requests.get(url, timeout=timeout, headers=headers, verify=verify)
    r.raise_for_status()
    return r.text


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """把细粒度 OHLCV 重采样成更大周期（开=首、高=最高、低=最低、收=末、量=和）。"""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule).agg(agg).dropna(subset=["open", "high", "low", "close"])


def fetch_yahoo_ohlcv(symbol: str = "AAPL", timeframe: str = "1d",
                      limit: int = 500) -> pd.DataFrame:
    """拉取 Yahoo 行情。symbol 用 Yahoo 代码：AAPL / 0700.HK / EURUSD=X / GC=F。"""
    resample_rule = _RESAMPLE.get(timeframe)
    iv = "60m" if resample_rule else _IV.get(timeframe, "1d")
    window_days = _WINDOW.get(iv, 1900)
    now = int(time.time())
    p1 = now - window_days * 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={p1}&period2={now}&interval={iv}&includePrePost=false")
    log.info("拉取 Yahoo %s %s(iv=%s) 窗口%d天", symbol, timeframe, iv, window_days)

    raw = json.loads(_http_get(url))
    chart = raw.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo 返回错误: {chart['error']}")
    result = (chart.get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        raise RuntimeError(f"Yahoo 未返回 {symbol} 的K线数据")

    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "ts": pd.to_datetime(ts, unit="s"),
        "open": q.get("open"), "high": q.get("high"),
        "low": q.get("low"), "close": q.get("close"), "volume": q.get("volume"),
    })
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).set_index("ts").sort_index()
    df["volume"] = df["volume"].fillna(0.0)
    if resample_rule:
        df = _resample(df, resample_rule)
    return df.tail(limit)


# ------------------------------------------------ 精选标的清单（供搜索）
_LISTS: dict[str, list[tuple[str, str]]] = {
    # (Yahoo代码, 中文名)
    "us": [
        ("AAPL", "苹果"), ("MSFT", "微软"), ("NVDA", "英伟达"), ("GOOGL", "谷歌"),
        ("AMZN", "亚马逊"), ("META", "Meta"), ("TSLA", "特斯拉"), ("AMD", "AMD"),
        ("NFLX", "奈飞"), ("AVGO", "博通"), ("COIN", "Coinbase"), ("MSTR", "MicroStrategy"),
        ("BABA", "阿里巴巴(美)"), ("PDD", "拼多多"), ("NIO", "蔚来"), ("SPY", "标普500ETF"),
        ("QQQ", "纳指100ETF"), ("DIA", "道指ETF"), ("IWM", "罗素2000ETF"),
    ],
    "hk": [
        ("0700.HK", "腾讯控股"), ("9988.HK", "阿里巴巴"), ("3690.HK", "美团"),
        ("9618.HK", "京东集团"), ("1810.HK", "小米集团"), ("0941.HK", "中国移动"),
        ("2318.HK", "中国平安"), ("1299.HK", "友邦保险"), ("0005.HK", "汇丰控股"),
        ("0388.HK", "香港交易所"), ("2020.HK", "安踏体育"), ("1024.HK", "快手"),
        ("9999.HK", "网易"), ("2269.HK", "药明生物"), ("0883.HK", "中国海洋石油"),
    ],
    "forex": [
        ("EURUSD=X", "欧元/美元"), ("GBPUSD=X", "英镑/美元"), ("USDJPY=X", "美元/日元"),
        ("USDCNY=X", "美元/人民币"), ("AUDUSD=X", "澳元/美元"), ("USDCAD=X", "美元/加元"),
        ("USDCHF=X", "美元/瑞郎"), ("NZDUSD=X", "纽元/美元"), ("EURGBP=X", "欧元/英镑"),
        ("EURJPY=X", "欧元/日元"), ("USDHKD=X", "美元/港币"), ("DX-Y.NYB", "美元指数"),
    ],
    "futures": [
        ("GC=F", "黄金"), ("SI=F", "白银"), ("HG=F", "铜"), ("CL=F", "WTI原油"),
        ("BZ=F", "布伦特原油"), ("NG=F", "天然气"), ("ES=F", "标普500期货"),
        ("NQ=F", "纳斯达克100期货"), ("YM=F", "道琼斯期货"), ("ZC=F", "玉米"),
        ("ZS=F", "大豆"), ("ZW=F", "小麦"), ("PL=F", "铂金"), ("PA=F", "钯金"),
    ],
}


def list_yahoo_symbols(market: str) -> list[tuple[str, str]]:
    """返回某市场的精选标的 [(代码, 名称), ...]。"""
    return _LISTS.get(market, [])

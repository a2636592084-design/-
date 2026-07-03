"""数据层：统一返回 OHLCV 的 pandas.DataFrame。

约定的 DataFrame 结构：
    index: DatetimeIndex（升序）
    columns: open, high, low, close, volume
"""
from .synthetic import synthetic_ohlcv
from .loader import get_ohlcv

__all__ = ["synthetic_ohlcv", "get_ohlcv"]

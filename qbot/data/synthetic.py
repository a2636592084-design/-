"""合成行情。用几何布朗运动 + 偶发跳空生成逼真的 OHLCV，
用于离线开发、单元测试、以及在你还没配好 API 时先把系统跑起来。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def synthetic_ohlcv(
    periods: int = 1500,
    start: str = "2020-01-01",
    freq: str = "1D",
    start_price: float = 100.0,
    annual_vol: float = 0.35,
    annual_drift: float = 0.08,
    periods_per_year: int = 252,
    seed: int = 42,
) -> pd.DataFrame:
    """生成一段可复现的行情。seed 固定 => 结果可复现。"""
    rng = np.random.default_rng(seed)
    dt = 1.0 / periods_per_year
    mu = annual_drift
    sigma = annual_vol

    # 对数收益：漂移 + 波动
    shocks = rng.normal(
        (mu - 0.5 * sigma**2) * dt,
        sigma * np.sqrt(dt),
        size=periods,
    )
    # 偶发跳空，模拟消息面冲击
    jumps = rng.normal(0, 0.05, size=periods) * (rng.random(periods) < 0.02)
    log_ret = shocks + jumps

    close = start_price * np.exp(np.cumsum(log_ret))
    open_ = np.concatenate([[start_price], close[:-1]])
    intrabar = np.abs(rng.normal(0, sigma * np.sqrt(dt), size=periods)) * close
    high = np.maximum(open_, close) + intrabar
    low = np.minimum(open_, close) - intrabar
    volume = rng.integers(1_000, 50_000, size=periods).astype(float)

    idx = pd.date_range(start=start, periods=periods, freq=freq)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )

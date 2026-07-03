"""策略基类。

一个策略只做一件事：看历史 K 线，输出"目标仓位"序列。
    +1.0 = 满仓做多   0.0 = 空仓   -1.0 = 满仓做空（现货默认不做空）
回测引擎和实盘引擎都消费同一个 target_position，保证一致性。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Signal:
    """某一时刻策略给出的最新决策，供实盘引擎使用。"""
    target_position: float           # 目标仓位比例 [-1, 1]
    reason: str = ""                 # 人类可读的理由，方便复盘
    meta: dict = field(default_factory=dict)


class Strategy:
    name: str = "base"
    # 只做多的市场（如 A股现货）把这个设为 True，引擎会把负仓位截断为 0
    long_only: bool = True

    def __init__(self, **params):
        self.params = params

    # ---- 子类必须实现 ----
    def generate_positions(self, df: pd.DataFrame) -> pd.Series:
        """输入 OHLCV，返回与 df 对齐的目标仓位序列（float，范围 [-1,1]）。"""
        raise NotImplementedError

    # ---- 通用能力 ----
    def latest_signal(self, df: pd.DataFrame) -> Signal:
        """取最新一根 K 线的决策，实盘引擎每个周期调用一次。"""
        pos = self.generate_positions(df)
        if self.long_only:
            pos = pos.clip(lower=0.0)
        last = float(pos.iloc[-1]) if len(pos) else 0.0
        return Signal(target_position=last, reason=f"{self.name} 最新目标仓位={last:.2f}")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} {self.params}>"

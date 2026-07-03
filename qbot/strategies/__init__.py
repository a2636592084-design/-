from .base import Strategy, Signal
from .ma_cross import MACrossStrategy
from .rsi_reversion import RSIReversionStrategy
from .donchian import DonchianBreakoutStrategy

# 策略注册表：面板/CLI 通过名字动态选择策略
REGISTRY: dict[str, type[Strategy]] = {
    "ma_cross": MACrossStrategy,
    "rsi_reversion": RSIReversionStrategy,
    "donchian": DonchianBreakoutStrategy,
}

__all__ = [
    "Strategy", "Signal", "REGISTRY",
    "MACrossStrategy", "RSIReversionStrategy", "DonchianBreakoutStrategy",
]

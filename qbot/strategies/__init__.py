from .base import Strategy, Signal
from .ma_cross import MACrossStrategy
from .rsi_reversion import RSIReversionStrategy
from .donchian import DonchianBreakoutStrategy
from .trend_stack import TrendStackStrategy
from .supertrend_strat import SuperTrendStrategy
from .bollinger_reversion import BollingerReversionStrategy
from .vwap_momentum import VWAPMomentumStrategy

# 策略注册表：面板/CLI 通过名字动态选择策略。
# 分类（按你的哲学，趋势/震荡互补，同类不冗余）：
#   趋势跟踪：ma_cross · donchian · trend_stack · supertrend
#   均值回归：rsi_reversion · bollinger_reversion
#   日内量价：vwap_momentum
REGISTRY: dict[str, type[Strategy]] = {
    "ma_cross": MACrossStrategy,
    "donchian": DonchianBreakoutStrategy,
    "trend_stack": TrendStackStrategy,
    "supertrend": SuperTrendStrategy,
    "rsi_reversion": RSIReversionStrategy,
    "bollinger_reversion": BollingerReversionStrategy,
    "vwap_momentum": VWAPMomentumStrategy,
}

__all__ = [
    "Strategy", "Signal", "REGISTRY",
    "MACrossStrategy", "RSIReversionStrategy", "DonchianBreakoutStrategy",
    "TrendStackStrategy", "SuperTrendStrategy", "BollingerReversionStrategy",
    "VWAPMomentumStrategy",
]

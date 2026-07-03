"""风控。策略负责"想赚"，风控负责"别死"。实盘里风控拥有一票否决权。

三道防线：
1. 单标的最大仓位上限（不把鸡蛋放一个篮子）。
2. 单笔止损（ATR 或固定百分比），亏到线就砍，不跟行情讲道理。
3. 组合级回撤熔断：整体回撤超过阈值，全部清仓、停止开新仓。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskConfig:
    max_position_per_symbol: float = 0.20   # 单标的最多占总资金 20%
    stop_loss_pct: float = 0.08             # 单笔止损 8%
    take_profit_pct: float = 0.25           # 单笔止盈 25%（可选）
    max_portfolio_drawdown: float = 0.20    # 组合回撤 20% 熔断
    risk_per_trade: float = 0.01            # 每笔最多亏损总资金的 1%（仓位反推）


@dataclass
class RiskDecision:
    allow: bool
    adjusted_position: float
    reason: str


class RiskManager:
    def __init__(self, config: RiskConfig | None = None):
        self.cfg = config or RiskConfig()
        self._halted = False
        self._peak_equity = 0.0

    def update_equity(self, equity: float) -> None:
        """跟踪资金曲线峰值，触发组合级熔断。"""
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity > 0:
            dd = equity / self._peak_equity - 1.0
            if dd <= -self.cfg.max_portfolio_drawdown:
                self._halted = True

    @property
    def halted(self) -> bool:
        return self._halted

    def position_size_by_risk(self, price: float, stop_price: float, equity: float) -> float:
        """按"每笔只赌 1% 本金"反推可买数量。这是职业交易员的仓位管理核心。"""
        risk_per_unit = abs(price - stop_price)
        if risk_per_unit <= 0:
            return 0.0
        dollars_at_risk = equity * self.cfg.risk_per_trade
        units = dollars_at_risk / risk_per_unit
        # 再受单标的仓位上限约束
        max_units_by_cap = (equity * self.cfg.max_position_per_symbol) / price
        return max(0.0, min(units, max_units_by_cap))

    def evaluate(self, target_position: float, equity: float) -> RiskDecision:
        """实盘引擎在下单前必须调用。返回是否放行 + 修正后的目标仓位。"""
        self.update_equity(equity)
        if self._halted:
            return RiskDecision(False, 0.0, "组合回撤熔断已触发：清仓并停止开新仓")

        capped = max(-self.cfg.max_position_per_symbol,
                     min(self.cfg.max_position_per_symbol, target_position))
        if capped != target_position:
            return RiskDecision(
                True, capped,
                f"目标仓位 {target_position:.2f} 超过单标的上限，收敛为 {capped:.2f}",
            )
        return RiskDecision(True, capped, "通过风控")

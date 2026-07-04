"""信号状态跟踪：只在信号真正变化的那一刻提醒一次，避免重复轰炸。

把每个"市场/标的/策略"的上次目标仓位落盘到 logs/signal_state.json，
重启后也不会把老信号重新推一遍。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..logger import get_logger

log = get_logger("qbot.notify.tracker")

_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "logs" / "signal_state.json"


def _bucket(pos: float) -> str:
    """把连续仓位离散成三档，避免 0.99↔1.00 的抖动被当成信号变化。"""
    if pos > 0.5:
        return "long"
    if pos < -0.5:
        return "short"
    return "flat"


class SignalTracker:
    def __init__(self, path: Path | None = None):
        self.path = path or _STATE_PATH
        self.state: dict[str, str] = {}
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                log.warning("读取信号状态失败(%s)，从空开始。", e)

    def update(self, key: str, pos: float) -> tuple[bool, str, str]:
        """返回 (是否变化, 旧档位, 新档位)。首次见到某 key 不算变化（只记录基线）。"""
        new = _bucket(pos)
        old = self.state.get(key)
        self.state[key] = new
        self._save()
        if old is None:
            return False, "unknown", new
        return (old != new), old, new

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(exist_ok=True)
            self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.warning("写入信号状态失败: %s", e)


LABEL = {"long": "🟢 买入/持有", "short": "🔴 做空", "flat": "⚪ 空仓/观望",
         "unknown": "（首次记录）"}

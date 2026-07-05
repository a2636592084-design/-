"""交易日志 + 真实资金曲线 + 实盘统计。

为什么这是"保命"功能：回测再好看也只是历史；**只有真实成交的记录**能回答
"这套系统到底在帮我赚还是亏"。本模块把每一笔成交落盘、按平均成本法算出每笔
**已实现盈亏**，再统计真实胜率/盈亏比/最大回撤——这些才是判断系统死活的依据。

落盘（可复现、重启不丢）：
  logs/journal_<mode>.jsonl   每行一个事件（fill 成交 / close 平仓已实现盈亏）
  logs/equity_<mode>.jsonl    每行一个权益快照 {ts, equity}

平均成本法支持多空：加仓摊薄成本，减仓/平仓/反手时结算已实现盈亏（已扣手续费）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .logger import get_logger

log = get_logger("qbot.journal")
_DIR = Path(__file__).resolve().parent.parent / "logs"


class TradeJournal:
    def __init__(self, mode: str):
        self.mode = mode
        self._pos: dict[str, dict] = {}      # symbol -> {amount(signed), avg}
        self._closed: list[dict] = []        # 每笔已实现盈亏
        self._equity: list[dict] = []        # [{ts, equity}]
        _DIR.mkdir(exist_ok=True)
        self._jfile = _DIR / f"journal_{mode}.jsonl"
        self._efile = _DIR / f"equity_{mode}.jsonl"
        self._load()

    # ---------- 持久化载入（重启续接） ----------
    def _load(self) -> None:
        if self._jfile.exists():
            for line in self._jfile.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                # 只回放 fill：_apply_fill 会重新生成 close(已实现盈亏)，
                # 故不能再读文件里的 close 行，否则会重复计入。
                if ev.get("type") == "fill":
                    self._apply_fill(ev["side"], ev["symbol"], ev["amount"],
                                     ev["price"], ev.get("fee", 0.0), persist=False)
        if self._efile.exists():
            for line in self._efile.read_text(encoding="utf-8").splitlines():
                try:
                    self._equity.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue

    def _append(self, file: Path, obj: dict) -> None:
        with file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # ---------- 记一笔成交（平均成本法，结算已实现盈亏） ----------
    def record_fill(self, side: str, symbol: str, amount: float, price: float,
                    fee: float = 0.0) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._append(self._jfile, {"type": "fill", "ts": ts, "side": side,
                                   "symbol": symbol, "amount": round(amount, 8),
                                   "price": round(price, 8), "fee": round(fee, 8)})
        self._apply_fill(side, symbol, amount, price, fee, persist=True, ts=ts)

    def _apply_fill(self, side, symbol, amount, price, fee, persist, ts="") -> None:
        signed = amount if side == "buy" else -amount
        st = self._pos.setdefault(symbol, {"amount": 0.0, "avg": 0.0})
        cur, avg = st["amount"], st["avg"]
        same_dir = (cur >= 0 and signed > 0) or (cur <= 0 and signed < 0) or cur == 0
        if same_dir:
            new_amt = cur + signed
            st["avg"] = ((avg * abs(cur)) + price * amount) / (abs(cur) + amount) \
                if (abs(cur) + amount) > 0 else price
            st["amount"] = new_amt
            return
        # 反向：先平掉重叠部分，结算已实现盈亏
        closing = min(amount, abs(cur))
        pnl = closing * (price - avg) if cur > 0 else closing * (avg - price)
        pnl -= fee                                   # 扣手续费（保守）
        rec = {"type": "close", "ts": ts, "symbol": symbol,
               "qty": round(closing, 8), "entry": round(avg, 8),
               "exit": round(price, 8), "pnl": round(pnl, 6)}
        self._closed.append(rec)
        if persist:
            self._append(self._jfile, rec)
        new_amt = cur + signed
        if abs(new_amt) < 1e-12:
            st["amount"], st["avg"] = 0.0, 0.0
        elif (new_amt > 0) != (cur > 0):            # 反手：剩余部分以本价开新仓
            st["amount"], st["avg"] = new_amt, price
        else:
            st["amount"] = new_amt                  # 部分平仓，成本不变

    # ---------- 权益快照 ----------
    def record_equity(self, equity: float, ts: str | None = None) -> None:
        if equity != equity or abs(equity) == float("inf"):   # 跳过 nan/inf，防污染曲线
            return
        ts = ts or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snap = {"ts": ts, "equity": round(float(equity), 4)}
        self._equity.append(snap)
        self._append(self._efile, snap)
        if len(self._equity) > 20000:               # 防无限增长（内存侧）
            self._equity = self._equity[-20000:]

    # ---------- 真实统计 ----------
    def stats(self, curve_points: int = 200) -> dict:
        closed = self._closed
        pnls = [c["pnl"] for c in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        eq = [e["equity"] for e in self._equity]
        max_dd = 0.0
        peak = None
        for v in eq:
            peak = v if peak is None else max(peak, v)
            if peak > 0:
                max_dd = min(max_dd, v / peak - 1.0)
        # 资金曲线降采样给前端画图
        curve = self._equity
        if len(curve) > curve_points:
            step = len(curve) / curve_points
            curve = [curve[int(i * step)] for i in range(curve_points)] + [curve[-1]]
        return {
            "closed_trades": len(closed),
            "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
            "realized_pnl": round(sum(pnls), 4),
            "avg_win": round(gross_win / len(wins), 4) if wins else 0.0,
            "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
            # 无亏损时无法算盈亏比，返回 None（前端按"全胜"处理），避免 JSON 出现 Infinity
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "max_drawdown": round(max_dd * 100, 2),
            "equity_curve": [{"ts": c["ts"], "equity": c["equity"]} for c in curve],
            "recent_closed": list(reversed(closed[-15:])),
        }

"""可视化面板后端（FastAPI）。

提供：
  /                 面板首页（资金曲线、指标、策略选择）
  /api/backtest     跑一次回测，返回资金曲线 + 指标（JSON）
  /api/symbols      列出可交易标的（加密全市场 / A股全市场）
启动：python run_dashboard.py   然后浏览器打开 http://127.0.0.1:8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from ..backtest import Backtester
from ..data.loader import get_ohlcv
from ..strategies import REGISTRY

app = FastAPI(title="qbot 量化面板")
TEMPLATES = Path(__file__).parent / "templates"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (TEMPLATES / "dashboard.html").read_text(encoding="utf-8")


@app.get("/api/strategies")
def strategies() -> JSONResponse:
    return JSONResponse(list(REGISTRY))


@app.get("/api/backtest")
def api_backtest(
    market: str = "synthetic",
    symbol: str = "DEMO",
    timeframe: str = "1d",
    strategy: str = "ma_cross",
    limit: int = 1200,
    ppy: int = 252,
) -> JSONResponse:
    if strategy not in REGISTRY:
        return JSONResponse({"error": f"未知策略 {strategy}"}, status_code=400)
    df = get_ohlcv(market, symbol, timeframe, limit=limit)
    result = Backtester(periods_per_year=ppy).run(df, REGISTRY[strategy]())
    equity = result.equity
    # 回撤序列，前端画水下曲线
    dd = (equity / equity.cummax() - 1.0)
    return JSONResponse({
        "dates": [str(d) for d in equity.index],
        "equity": [round(float(x), 2) for x in equity.values],
        "price": [round(float(x), 4) for x in result.price.values],
        "position": [float(x) for x in result.positions.values],
        "drawdown": [round(float(x), 4) for x in dd.values],
        "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in result.metrics.items()},
    })


@app.get("/api/symbols")
def api_symbols(market: str = "crypto", quote: str = "USDT") -> JSONResponse:
    try:
        if market == "crypto":
            from ..data.crypto import list_okx_symbols
            return JSONResponse(list_okx_symbols(quote)[:500])
        if market == "ashare":
            from ..data.ashare import list_ashare_symbols
            df = list_ashare_symbols()
            return JSONResponse(df.head(500).to_dict("records"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse([])

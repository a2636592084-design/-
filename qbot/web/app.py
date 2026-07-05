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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..backtest import Backtester
from ..data.loader import get_ohlcv
from ..strategies import REGISTRY

app = FastAPI(title="qbot 量化面板")
TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


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


def _f(x, default=0.0):
    """nan/inf 安全的 float 转换，避免 JSON 序列化炸掉。"""
    try:
        v = float(x)
        return v if v == v and abs(v) != float("inf") else default
    except (TypeError, ValueError):
        return default


@app.get("/api/scan")
def api_scan(
    market: str = "crypto",
    symbols: str = "BTC/USDT,ETH/USDT",
    strategy: str = "regime_switch",
    timeframe: str = "1d",
    atr_stop: float | None = None,
) -> JSONResponse:
    """对一篮子标的批量算最新信号，供实时盯盘。逐标的拉真实数据，不回退合成。"""
    if strategy not in REGISTRY:
        return JSONResponse({"error": f"未知策略 {strategy}"}, status_code=400)
    from ..risk.stops import apply_atr_trailing_stop
    from ..strategies.indicators import adx, rsi

    strat = REGISTRY[strategy]()
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    rows = []
    for sym in syms:
        try:
            df = get_ohlcv(market, sym, timeframe, limit=400, fallback_synthetic=False)
            pos = strat.generate_positions(df).fillna(0.0)
            if strat.long_only:
                pos = pos.clip(lower=0.0)
            if atr_stop:
                pos = apply_atr_trailing_stop(df, pos, atr_stop)
            target = _f(pos.iloc[-1])
            prev = _f(pos.iloc[-2]) if len(pos) > 1 else 0.0
            a = adx(df)[0]
            # 共振策略额外给出"共振分"，让用户看到多指标合力强弱
            score = None
            if hasattr(strat, "explain"):
                try:
                    score = _f(strat.explain(df).get("score"), default=None)
                except Exception:  # noqa: BLE001
                    score = None
            rows.append({
                "symbol": sym,
                "price": _f(df["close"].iloc[-1]),
                "change": _f(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)
                if len(df) > 1 else 0.0,
                "target": target,
                "prev": prev,
                "flipped": abs(target - prev) > 1e-9,   # 信号刚发生变化 → 重点关注
                "adx": _f(a.iloc[-1]),
                "rsi": _f(rsi(df["close"]).iloc[-1], default=50.0),
                "score": score,
                "date": str(df.index[-1].date()),
                "bars": len(df),
            })
        except Exception as e:  # noqa: BLE001
            rows.append({"symbol": sym, "error": str(e)[:120]})
    return JSONResponse(rows)


import time as _time

_OHLCV_CACHE: dict = {}          # (market,symbol,tf) -> (ts, df)
_CACHE_TTL = 30.0                # 30秒内切回同一标的/周期直接命中，秒开


def _terminal_ohlcv(market: str, symbol: str, timeframe: str, limit: int = 500):
    """终端专用带缓存的行情拉取（K线与分析共用一次，减少 OKX 请求、提速）。"""
    key = (market, symbol, timeframe)
    hit = _OHLCV_CACHE.get(key)
    if hit and (_time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    df = get_ohlcv(market, symbol, timeframe, limit=limit, fallback_synthetic=False)
    _OHLCV_CACHE[key] = (_time.time(), df)
    if len(_OHLCV_CACHE) > 200:   # 简单上限，防内存无限增长
        _OHLCV_CACHE.pop(next(iter(_OHLCV_CACHE)))
    return df


def _df_to_klines(df) -> list:
    return [{
        "timestamp": int(ts.timestamp() * 1000),
        "open": round(float(r.open), 6), "high": round(float(r.high), 6),
        "low": round(float(r.low), 6), "close": round(float(r.close), 6),
        "volume": round(float(r.volume), 4),
    } for ts, r in df.iterrows()]


@app.get("/api/klines")
def api_klines(market: str = "crypto", symbol: str = "BTC/USDT",
               timeframe: str = "1d", limit: int = 500,
               live: int = 0, before: int = 0) -> JSONResponse:
    """行情终端K线：返回 KLineChart 需要的 [{timestamp,open,high,low,close,volume}]。

    live=1 只取最近几根、绕过缓存（用于实时刷新，让最后一根随行情跳动）。
    before=<毫秒时间戳> 取更早的一页历史（图表向左滚动时按需加载更多，仅加密支持）。
    """
    try:
        if before and market == "crypto":
            # 向左加载更多历史（早于 before 的一页）
            from ..data.crypto import fetch_okx_ohlcv_before
            df = fetch_okx_ohlcv_before(symbol, timeframe, before_ms=before, limit=300)
            return JSONResponse({"klines": _df_to_klines(df)})
        if before:                       # 非加密暂不支持历史分页，返回空表示到头
            return JSONResponse({"klines": []})
        if live:
            # 实时刷新：绕过缓存，拉最近几根真实行情
            df = get_ohlcv(market, symbol, timeframe, limit=3, fallback_synthetic=False)
        else:
            df = _terminal_ohlcv(market, symbol, timeframe, limit=limit)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:150]}, status_code=502)
    return JSONResponse({"klines": _df_to_klines(df)})


@app.get("/api/search")
def api_search(q: str = "", market: str = "crypto") -> JSONResponse:
    """标的搜索：加密(OKX全市场) + A股。其它市场二期接入。"""
    q = q.strip().upper()
    results = []
    try:
        if market == "crypto":
            from ..data.crypto import list_okx_symbols
            syms = list_okx_symbols("USDT")
            for s in syms:
                if not q or q in s.upper():
                    results.append({"name": s.split("/")[0], "code": s})
        elif market == "ashare":
            from ..universe import ashare_universe
            for code in ashare_universe(top=300):
                if not q or q in code:
                    results.append({"name": code, "code": code})
        elif market in ("us", "hk", "forex", "futures"):
            from ..data.yahoo import list_yahoo_symbols
            for code, name in list_yahoo_symbols(market):
                if not q or q in code.upper() or q in name.upper():
                    results.append({"name": name, "code": code})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:150], "results": []})
    return JSONResponse({"results": results[:60], "total": len(results)})


@app.get("/api/analyze")
def api_analyze(market: str = "crypto", symbol: str = "BTC/USDT",
                timeframe: str = "1d", limit: int = 400) -> JSONResponse:
    """行情终端右侧：共振投票 + 结构化分析（阻力/支撑/止损/分维度/文字解读）。"""
    try:
        df = _terminal_ohlcv(market, symbol, timeframe, limit=max(limit, 300))
        from .analysis import analyze
        res = analyze(df)
        # 配了 DeepSeek key 就用大模型解读，否则保持规则版（失败也回退）
        from .llm import deepseek_analyze, deepseek_available
        if deepseek_available():
            try:
                res["text"] = deepseek_analyze(symbol, timeframe, res)
                res["source"] = "DeepSeek 大模型"
            except Exception as e:  # noqa: BLE001
                res["source"] = f"规则引擎（DeepSeek 调用失败：{str(e)[:60]}）"
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:150]}, status_code=502)


@app.get("/api/structure")
def api_structure(market: str = "crypto", symbol: str = "BTC/USDT",
                  timeframe: str = "1d", limit: int = 300) -> JSONResponse:
    """高阶结构分析：道氏 / SMC / 缠论（分型-笔-中枢）。"""
    try:
        df = _terminal_ohlcv(market, symbol, timeframe, limit=max(limit, 120))
        from ..strategies.structure import structure_report
        return JSONResponse(structure_report(df))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:150]}, status_code=502)


@app.get("/api/resonance")
def api_resonance(market: str = "crypto", symbol: str = "BTC/USDT",
                  timeframe: str = "1d", limit: int = 300) -> JSONResponse:
    """共振面板：~19 个指标对当前一根的多/空/中投票 + 多空计数。"""
    try:
        df = _terminal_ohlcv(market, symbol, timeframe, limit=max(limit, 120))
        from ..strategies.resonance import resonance_votes
        return JSONResponse(resonance_votes(df))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:150]}, status_code=502)


@app.get("/api/overlays")
def api_overlays(market: str = "crypto", symbol: str = "BTC/USDT",
                 timeframe: str = "1d", limit: int = 300) -> JSONResponse:
    """画在K线图上的结构图形：缠论(笔/中枢) / SMC(BOS-CHoCH/订单块/FVG) / 道氏(摆动点)。"""
    try:
        df = _terminal_ohlcv(market, symbol, timeframe, limit=max(limit, 120))
        from ..strategies.structure import structure_overlays
        return JSONResponse(structure_overlays(df))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:150]}, status_code=502)


@app.get("/api/signals")
def api_signals(market: str = "crypto", symbol: str = "BTC/USDT",
                timeframe: str = "1d", limit: int = 300) -> JSONResponse:
    """K线图信号标注：共振策略的买卖点 + 阻力/支撑/止损线。"""
    try:
        df = _terminal_ohlcv(market, symbol, timeframe, limit=limit)
        from .analysis import analyze
        from ..strategies.confluence import ConfluenceStrategy
        pos = ConfluenceStrategy(allow_short=True).generate_positions(df).fillna(0.0)
        vals = pos.to_numpy()
        idx = df.index
        close = df["close"].to_numpy()
        markers = []
        prev = 0.0
        for i in range(len(vals)):
            cur = vals[i]
            if cur == prev:
                continue
            ts = int(idx[i].timestamp() * 1000)
            price = float(close[i])
            if prev <= 0 and cur > 0:
                markers.append({"timestamp": ts, "price": price, "type": "buy", "text": "买"})
            elif prev >= 0 and cur < 0:
                markers.append({"timestamp": ts, "price": price, "type": "short", "text": "空"})
            elif prev > 0 and cur <= 0:
                markers.append({"timestamp": ts, "price": price, "type": "sell", "text": "平多"})
            elif prev < 0 and cur >= 0:
                markers.append({"timestamp": ts, "price": price, "type": "cover", "text": "平空"})
            prev = cur
        a = analyze(df)
        return JSONResponse({
            "markers": markers[-40:],   # 只标最近若干个，避免刷屏
            "levels": {"resistance": a["resistance"], "support": a["support"], "stop": a["stop"]},
        })
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:150]}, status_code=502)


class EngineCfg(BaseModel):
    mode: str = "paper"
    market: str = "crypto"
    broker: str = "paper"
    trade_type: str = "spot"
    leverage: int = 3
    allow_short: bool = False
    strategy: str = "confluence"
    top: int = 40
    max_positions: int = 6
    timeframe: str = "4h"
    poll: int = 60
    stop_loss: float = 0.08
    take_profit: float = 0.0
    breakeven: float = 0.05
    trailing_stop: float = 0.06
    atr_stop_mult: float = 2.5
    cooldown: int = 3
    exchange_stops: bool = True
    quality: bool = True
    notify: bool = False
    i_understand_risk: bool = False
    capital: float = 100_000.0


@app.post("/api/engine/start")
def api_engine_start(cfg: EngineCfg) -> JSONResponse:
    """网页"启动"按钮：在面板进程内后台线程启动组合引擎。"""
    from .runner import RUNNER
    data = cfg.model_dump() if hasattr(cfg, "model_dump") else cfg.dict()
    try:
        info = RUNNER.start(data)
        return JSONResponse({"ok": True, "info": info})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:200]})


@app.post("/api/engine/stop")
def api_engine_stop(mode: str = "paper") -> JSONResponse:
    from .runner import RUNNER
    RUNNER.stop(mode)
    return JSONResponse({"ok": True})


@app.get("/api/engine/status")
def api_engine_status(mode: str = "paper") -> JSONResponse:
    from .runner import RUNNER
    return JSONResponse(RUNNER.status(mode))


@app.post("/api/portfolio_backtest")
def api_portfolio_backtest(cfg: EngineCfg) -> JSONResponse:
    """用与"启动"完全相同的配置，把整套组合规则拿历史数据快速跑一遍。"""
    from ..backtest.portfolio_bt import backtest_portfolio
    from ..strategies import REGISTRY
    from ..universe import get_universe
    c = cfg.model_dump() if hasattr(cfg, "model_dump") else cfg.dict()
    try:
        market = "ashare" if c["mode"] == "ashare" else "crypto"
        if market == "ashare":
            c["timeframe"] = "1d"        # A股数据仅日线
        types = ("swap",) if c["trade_type"] == "swap" else ("spot",)
        universe = get_universe(market, top=min(int(c["top"]), 30), types=types,
                                quote="USDT", quality=bool(c.get("quality", True)))
        if not universe:
            return JSONResponse({"error": "未取到标的，检查网络/代理。"})
        allow_short = bool(c["allow_short"]) and c["strategy"] == "confluence"

        def factory():
            return (REGISTRY[c["strategy"]](allow_short=True) if allow_short
                    else REGISTRY[c["strategy"]]())

        res = backtest_portfolio(
            market, universe, factory, timeframe=c["timeframe"],
            max_positions=int(c["max_positions"]), stop_loss=float(c["stop_loss"]),
            take_profit=float(c["take_profit"]), breakeven=float(c["breakeven"]),
            trailing_stop=float(c["trailing_stop"]), atr_stop_mult=float(c["atr_stop_mult"]),
            cooldown=int(c["cooldown"]))
        res["capped_top"] = min(int(c["top"]), 30)
        return JSONResponse(res)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:200]})


@app.get("/api/portfolio")
def api_portfolio(mode: str = "paper") -> JSONResponse:
    """读取组合引擎落盘的状态，供面板三个交易页展示。"""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent.parent / "logs" / f"portfolio_{mode}.json"
    if not path.exists():
        return JSONResponse({"running": False, "reason": "引擎未启动或还没跑出第一轮。"
                             f" 请先运行 run_portfolio.py（mode={mode}）。"})
    try:
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"running": False, "reason": f"读取状态失败: {e}"})


@app.get("/api/universe")
def api_universe(market: str = "crypto", top: int = 30,
                 types: str = "spot") -> JSONResponse:
    """列出全市场标的数量与 Top 预览。"""
    try:
        from ..universe import get_universe
        syms = get_universe(market, top=top, types=tuple(types.split(",")))
        return JSONResponse({"count": len(syms), "symbols": syms})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)}, status_code=502)


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

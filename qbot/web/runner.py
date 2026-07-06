"""在面板进程内以后台线程运行组合引擎，供网页"启动/停止"按钮控制。

单进程 uvicorn 下用守护线程即可：引擎在线程里跑 run_forever()，状态照常落盘到
logs/portfolio_<mode>.json（面板读它显示）。停止=把 engine.state.running 置 False，
可中断睡眠让它 ≤1 秒退出。每个 mode(paper/crypto/ashare) 同时只允许一个引擎。
"""
from __future__ import annotations

import threading

from ..engine.build import build_engine
from ..logger import get_logger

log = get_logger("qbot.web.runner")


class EngineRunner:
    def __init__(self) -> None:
        self._engines: dict = {}
        self._threads: dict = {}
        self._info: dict = {}
        self._error: dict = {}
        self._lock = threading.Lock()

    def is_running(self, mode: str) -> bool:
        t = self._threads.get(mode)
        return bool(t and t.is_alive())

    def start(self, cfg: dict) -> dict:
        mode = (cfg or {}).get("mode", "paper")
        with self._lock:
            if self.is_running(mode):
                raise RuntimeError(f"{mode} 引擎已在运行，请先停止。")
            engine, info = build_engine(cfg)      # 构建失败(如未配key)会抛 ValueError
            self._error.pop(mode, None)
            t = threading.Thread(target=self._run, args=(mode, engine),
                                 daemon=True, name=f"engine-{mode}")
            self._engines[mode] = engine
            self._threads[mode] = t
            self._info[mode] = info
            t.start()
            log.info("引擎已启动 [%s]：%s", mode, info)
            return info

    def _run(self, mode: str, engine) -> None:
        try:
            engine.run_forever()
        except Exception as e:  # noqa: BLE001
            self._error[mode] = str(e)[:200]
            log.exception("引擎线程异常退出 [%s]: %s", mode, e)

    def stop(self, mode: str) -> bool:
        eng = self._engines.get(mode)
        if eng is not None:
            eng.state.running = False
            log.info("已请求停止引擎 [%s]", mode)
        return True

    def status(self, mode: str) -> dict:
        return {"running": self.is_running(mode),
                "info": self._info.get(mode),
                "error": self._error.get(mode)}


RUNNER = EngineRunner()

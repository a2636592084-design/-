"""统一日志。终端可读 + 落盘，方便复盘每一笔操作。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def get_logger(name: str = "qbot", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:  # 避免重复添加 handler
        return logger
    logger.setLevel(level)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(ch)

    fh = logging.FileHandler(_LOG_DIR / "qbot.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    logger.addHandler(fh)

    logger.propagate = False
    return logger

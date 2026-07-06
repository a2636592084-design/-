"""配置加载。API 密钥只从环境变量/.env 读取，绝不写进代码或提交到 git。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """极简 .env 解析，避免强依赖 python-dotenv。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


@dataclass
class OKXCredentials:
    api_key: str = field(default_factory=lambda: os.getenv("OKX_API_KEY", ""))
    secret: str = field(default_factory=lambda: os.getenv("OKX_API_SECRET", ""))
    passphrase: str = field(default_factory=lambda: os.getenv("OKX_PASSPHRASE", ""))
    # OKX 有模拟盘环境；默认走模拟盘，保命。
    demo: bool = field(default_factory=lambda: os.getenv("OKX_DEMO", "1") == "1")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret and self.passphrase)


@dataclass
class AppConfig:
    raw: dict = field(default_factory=dict)
    okx: OKXCredentials = field(default_factory=OKXCredentials)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        path = Path(path) if path else ROOT / "config.yaml"
        raw: dict = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        elif (ROOT / "config.example.yaml").exists():
            raw = yaml.safe_load(
                (ROOT / "config.example.yaml").read_text(encoding="utf-8")
            ) or {}
        return cls(raw=raw)

    def get(self, *keys, default=None):
        node = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

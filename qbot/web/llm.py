"""大模型解读（DeepSeek，OpenAI 兼容接口）。

有 .env 的 DEEPSEEK_API_KEY 才启用；否则行情终端自动回退到规则版解读。
只用结构化事实（价位/评分/投票）喂给模型，让它写一段中文分析，不做投资保证。
密钥只从环境变量读，绝不写进代码或日志。
"""
from __future__ import annotations

import os
import time

from ..logger import get_logger

log = get_logger("qbot.llm")

_CACHE: dict = {}          # key -> (ts, text)，省钱：相同标的短期不重复调用
_TTL = 300.0              # 5 分钟


def deepseek_available() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY"))


def _post(url: str, headers: dict, json_body: dict, timeout: int = 30) -> dict:
    import requests
    ca = os.environ.get("SSL_CERT_FILE")
    if not ca or not os.path.exists(ca):
        ca = "/root/.ccr/ca-bundle.crt"
    verify = ca if os.path.exists(ca) else True
    r = requests.post(url, headers=headers, json=json_body, timeout=timeout, verify=verify)
    r.raise_for_status()
    return r.json()


def deepseek_analyze(symbol: str, timeframe: str, facts: dict) -> str:
    """把结构化事实交给 DeepSeek，返回一段中文分析。失败则抛异常由上层回退。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")

    cache_key = (symbol, timeframe, facts.get("gauge"))
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]

    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    votes = "，".join(f"{k}{'看多' if v > 0 else '看空' if v < 0 else '中性'}"
                      for k, v in facts.get("votes", {}).items())
    dims = "，".join(f"{k}{v}" for k, v in facts.get("dims", {}).items())
    user = (
        f"标的 {symbol}（{timeframe}）。技术面结构化数据：\n"
        f"现价 {facts.get('price')}，阻力 {facts.get('resistance')}，支撑 {facts.get('support')}，"
        f"参考止损 {facts.get('stop')}。\n"
        f"多空共振评分 {facts.get('gauge')}/100（50中性），倾向：{facts.get('verdict')}。\n"
        f"分维度评分：{dims}。\n多指标投票：{votes}。\n"
        f"请用中文写一段150字以内的专业解读：先点明当前多空格局与核心矛盾，"
        f"再给出关键价位的操作参考（突破/跌破怎么看），最后一句风险提示。客观、不夸大、不做收益保证。"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严谨的量化交易分析师，只基于给定数据客观分析，不编造，不承诺收益。"},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 400,
        "stream": False,
    }
    data = _post(f"{base}/chat/completions",
                 {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                 body)
    text = data["choices"][0]["message"]["content"].strip()
    _CACHE[cache_key] = (time.time(), text)
    return text

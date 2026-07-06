"""通知系统测试：状态跟踪防重复、渠道选择、单渠道失败隔离。

全程不发真实网络请求（用假 Notifier / 只验证构造）。
"""
from __future__ import annotations

import importlib

from qbot.notify import notify_all
from qbot.notify.base import Notifier
from qbot.notify.tracker import SignalTracker


def test_tracker_first_seen_not_flip(tmp_path):
    t = SignalTracker(path=tmp_path / "s.json")
    changed, old, new = t.update("BTC", 1.0)
    assert changed is False and new == "long"   # 首次只记基线，不算变化


def test_tracker_flip_only_on_change(tmp_path):
    t = SignalTracker(path=tmp_path / "s.json")
    t.update("BTC", 0.0)                      # 基线 flat
    assert t.update("BTC", 1.0)[0] is True    # flat→long 变化
    assert t.update("BTC", 1.0)[0] is False   # long→long 不变
    assert t.update("BTC", 0.9)[0] is False   # 仍在 long 档，不算变化(防抖动)
    assert t.update("BTC", 0.0)[0] is True    # long→flat 变化


def test_tracker_persists(tmp_path):
    p = tmp_path / "s.json"
    SignalTracker(path=p).update("ETH", 1.0)
    # 新实例读盘后应记得基线，flat 化才算变化
    assert SignalTracker(path=p).update("ETH", 1.0)[0] is False


def test_build_from_env_default(monkeypatch):
    """不设任何密钥时，只有 console 渠道。"""
    for k in ["SERVERCHAN_KEY", "PUSHPLUS_TOKEN", "TELEGRAM_BOT_TOKEN",
              "TELEGRAM_CHAT_ID", "SMTP_HOST", "SMTP_TO"]:
        monkeypatch.delenv(k, raising=False)
    import qbot.notify as n
    importlib.reload(n)
    names = [x.name for x in n.build_from_env()]
    assert names == ["console"]


def test_build_from_env_enables_wechat(monkeypatch):
    monkeypatch.setenv("PUSHPLUS_TOKEN", "faketoken")
    import qbot.notify as n
    importlib.reload(n)
    names = [x.name for x in n.build_from_env()]
    assert "wechat" in names


def test_notify_all_isolates_failure():
    """一个渠道抛错，不应影响其它渠道，也不抛出。"""
    sent = []

    class Good(Notifier):
        name = "good"
        def send(self, title, message): sent.append(title)

    class Bad(Notifier):
        name = "bad"
        def send(self, title, message): raise RuntimeError("boom")

    notify_all("t", "m", [Bad(), Good()])   # 不应抛异常
    assert sent == ["t"]                      # Good 仍然收到了

"""ダッシュボード起動まわりのテスト（ネットワーク不要）。"""

from __future__ import annotations

import pytest

from suzuleague import dashboard


class FlakyBridge:
    """最初の数回は接続に失敗する（Render が寝ているときの再現）。"""

    cloud_host = "wss://example.onrender.com"

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def connect(self) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("Connection timed out")


def test_wakes_server_and_retries(monkeypatch):
    woken = []
    monkeypatch.setattr(dashboard, "wake_server", lambda url: woken.append(url) or True)
    bridge = FlakyBridge(failures=1)
    dashboard.connect_with_wake(bridge)
    assert bridge.calls == 2
    assert woken == ["https://example.onrender.com"]


def test_gives_up_after_attempts(monkeypatch):
    monkeypatch.setattr(dashboard, "wake_server", lambda url: False)
    bridge = FlakyBridge(failures=5)
    with pytest.raises(TimeoutError):
        dashboard.connect_with_wake(bridge, attempts=3)
    assert bridge.calls == 3

"""cloud への送信（CloudBridge）のテスト（ネットワーク不要）。

値の型と送る順番は、Scratch 側の動きにそのまま響く。にせものの接続で送った中身を確かめる。
"""

from __future__ import annotations

from suzuleague.cloud import CloudBridge
from suzuleague.engine import Snapshot, State


class FakeCloud:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    def set_var(self, name: str, value: object) -> None:
        self.sent.append((name, value))


def make_bridge() -> tuple[CloudBridge, FakeCloud]:
    bridge = CloudBridge("room", cloud_host="ws://localhost:9080")
    bridge.cloud = FakeCloud()  # type: ignore[assignment]
    return bridge, bridge.cloud  # type: ignore[return-value]


class TestCloudBridgeSend:
    def test_values_are_sent_as_strings(self):
        """数値で送ると Scratch 側でコスチュームが番号で選ばれ、問題番号が1つずれる。"""
        bridge, cloud = make_bridge()
        bridge.push(Snapshot(State.QUESTION, 1, 3, 3, -1, 80))
        assert dict(cloud.sent)["P2S_ROUND"] == "3"
        assert all(isinstance(value, str) for _, value in cloud.sent)
        assert cloud.sent[-1] == ("P2S_SEQ", "1")  # SEQ は最後

    def test_answer_is_sent_alone_and_kept_for_resync(self):
        bridge, cloud = make_bridge()
        bridge.push(Snapshot(State.ANSWERING, 1, 3, 3, -1, 80))
        cloud.sent.clear()
        bridge.push_answer(Snapshot(State.ANSWERING, 1, 3, 3, -1, 80, answer=45))
        assert cloud.sent == [("P2S_ANSWER", "45")]
        cloud.sent.clear()
        bridge.resync()
        assert ("P2S_ANSWER", "45") in cloud.sent
        assert cloud.sent[-1] == ("P2S_SEQ", "2")

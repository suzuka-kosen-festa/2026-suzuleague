"""接続先cloudサーバの解決ロジックのテスト（ネットワーク不要）。

本番はセルフホストのサーバに切り替えるため、URLの取り違えが
そのままイベント当日の事故になる。ここで境界を固めておく。
"""

from __future__ import annotations

import pytest

from suzuleague.cloud import (
    ENV_CLOUD_HOST,
    TW_CLOUD_HOST,
    normalize_cloud_host,
    resolve_cloud_host,
)


class TestResolveCloudHost:
    def test_default_is_public_server(self, monkeypatch):
        monkeypatch.delenv(ENV_CLOUD_HOST, raising=False)
        assert resolve_cloud_host() == TW_CLOUD_HOST

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv(ENV_CLOUD_HOST, "wss://example.onrender.com")
        assert resolve_cloud_host() == "wss://example.onrender.com"

    def test_cli_wins_over_env(self, monkeypatch):
        monkeypatch.setenv(ENV_CLOUD_HOST, "wss://from-env.example")
        assert resolve_cloud_host("wss://from-cli.example") == "wss://from-cli.example"

    def test_empty_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(ENV_CLOUD_HOST, "")
        assert resolve_cloud_host() == TW_CLOUD_HOST


class TestNormalizeCloudHost:
    def test_https_is_rewritten_to_wss(self):
        # Renderの管理画面は https:// のURLを表示するのでそのまま貼れるようにする
        assert (
            normalize_cloud_host("https://suzuleague.onrender.com")
            == "wss://suzuleague.onrender.com"
        )

    def test_http_localhost_is_rewritten_to_ws(self):
        assert normalize_cloud_host("http://localhost:9080") == "ws://localhost:9080"

    def test_trailing_slash_and_spaces_are_stripped(self):
        assert normalize_cloud_host("  wss://a.example/  ") == "wss://a.example"

    def test_plain_ws_allowed_for_localhost(self):
        assert normalize_cloud_host("ws://localhost:9080") == "ws://localhost:9080"
        assert normalize_cloud_host("ws://127.0.0.1:9080") == "ws://127.0.0.1:9080"

    def test_plain_ws_rejected_for_remote_host(self):
        # TurboWarpのページはHTTPS配信なので平文wsはmixed contentでブロックされる
        with pytest.raises(ValueError, match="wss://"):
            normalize_cloud_host("ws://suzuleague.onrender.com")

    def test_unknown_scheme_rejected(self):
        with pytest.raises(ValueError, match="ws://"):
            normalize_cloud_host("suzuleague.onrender.com")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="空"):
            normalize_cloud_host("   ")


class TestCloudReceiver:
    """送信用の接続から変数の更新を取り出す処理（ネットワーク不要）。"""

    def make(self):
        import threading

        from suzuleague.cloud import CloudReceiver

        got = []
        receiver = CloudReceiver(None, lambda n, v: got.append((n, v)), threading.Event())
        return receiver, got

    def test_parses_multiple_messages_in_one_frame(self):
        receiver, got = self.make()
        frame = (
            '{"method":"set","name":"☁ S2P_ANSWER","value":55}\n'
            '{"method":"set","name":"☁ S2P_SEQ","value":3}'
        )
        receiver.handle_lines(frame, skip=False)
        assert got == [("S2P_ANSWER", 55), ("S2P_SEQ", 3)]

    def test_initial_dump_is_skipped(self):
        """接続直後のまとめ送りには古い回答が入っているので使わない。"""
        receiver, got = self.make()
        receiver.handle_lines('{"method":"set","name":"☁ S2P_ANSWER","value":66}', skip=True)
        assert got == []

    def test_ignores_other_messages_and_garbage(self):
        receiver, got = self.make()
        receiver.handle_lines('{"method":"rename","name":"☁ A"}\nnot json\n', skip=False)
        assert got == []

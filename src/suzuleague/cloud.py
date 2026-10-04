"""TurboWarp cloudとの接続を担うブリッジ層。

scratchattachのTwCloud（ログイン不要）を使い、
- エンジンの状態をP2S変数群としてpushする
- Scratch側からのS2P変数の更新（回答・ACK）をイベントで受信する

TurboWarp cloudはインメモリのため全員切断で値が消える。
resync()でいつでも最新状態を再送できるようにしている。
"""

from __future__ import annotations

import json
import os
import select
import ssl
import threading
import time
from collections.abc import Callable

import scratchattach as sa
import websocket

from . import protocol
from .engine import Snapshot

# 開発中は実プロジェクトの部屋を汚さないよう専用ルームIDを使う
# （TurboWarp cloudサーバは任意のIDで部屋を作れることを確認済み）。
# 本番はScratch担当がリミックスしたプロジェクトのIDに差し替える。
DEFAULT_PROJECT_ID = "suzuleague-dev"
ENV_PROJECT_ID = "SUZULEAGUE_PROJECT_ID"
HEARTBEAT_INTERVAL = 15.0  # 秒

# TurboWarp公式の公開サーバ。開発中の既定の接続先。
# 本番は1部屋128クライアントの上限を避けるためセルフホストのサーバに切り替える
# （docs/architecture.md「同時接続数の上限とセルフホスト方針」参照）。
TW_CLOUD_HOST = "wss://clouddata.turbowarp.org"
ENV_CLOUD_HOST = "SUZULEAGUE_CLOUD_HOST"


def resolve_project_id(cli_value: str | None = None) -> str:
    """プロジェクトIDを CLI引数 > 環境変数 > デフォルト の順で決める。"""
    return cli_value or os.environ.get(ENV_PROJECT_ID) or DEFAULT_PROJECT_ID


def resolve_cloud_host(cli_value: str | None = None) -> str:
    """接続先cloudサーバを CLI引数 > 環境変数 > 公開サーバ の順で決める。

    ホスティングサービスの管理画面は `https://...` 形式のURLを表示するため、
    それをそのまま渡されても動くよう ws/wss スキームへ読み替える。
    """
    return normalize_cloud_host(
        cli_value or os.environ.get(ENV_CLOUD_HOST) or TW_CLOUD_HOST
    )


def normalize_cloud_host(host: str) -> str:
    """cloudサーバのURLを検証し、ws/wssスキームに正規化する。"""
    host = host.strip().rstrip("/")
    if not host:
        raise ValueError("cloudサーバのURLが空です")
    # Renderなどの管理画面からコピーしたhttp(s)://をそのまま使えるようにする
    if host.startswith("https://"):
        host = "wss://" + host.removeprefix("https://")
    elif host.startswith("http://"):
        host = "ws://" + host.removeprefix("http://")
    if not host.startswith(("ws://", "wss://")):
        raise ValueError(
            f"cloudサーバのURLは ws:// か wss:// で始まる必要があります: {host!r}"
        )
    if host.startswith("ws://") and not _is_local(host):
        # TurboWarpのページはHTTPSで配信されるため、平文wsはブラウザの
        # mixed contentブロックに引っかかりScratch側からは繋がらない
        raise ValueError(
            f"外部ホストへは wss:// を使ってください（TLSなしはScratch側から繋がりません）: {host!r}"
        )
    return host


def _is_local(host: str) -> bool:
    netloc = host.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    return netloc in ("localhost", "127.0.0.1", "::1", "[::1]")


def fetch_all_vars(
    project_id: str,
    *,
    cloud_host: str | None = None,
    timeout: float = 2.0,
    username: str = "suzuleague",
) -> dict[str, str]:
    """現在のcloud変数の値をすべて取得する（変数名は☁なし）。

    TurboWarpのcloudサーバは新規接続時に現在値をまとめて送ってくるが、
    scratchattachのget_var/CloudRecorderは接続直後500ms以内のメッセージを
    捨てる実装のためこの初期ダンプを受け取れない。そこで生のWebSocketで
    短時間接続して初期ダンプを読む。
    """
    ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
    values: dict[str, str] = {}
    try:
        ws.connect(
            resolve_cloud_host(cloud_host),
            origin="https://turbowarp.org",
            timeout=timeout,
            header={"User-Agent": "scratchattach/2.0.0 (suzuleague)"},
        )
        ws.send(
            json.dumps(
                {"method": "handshake", "project_id": project_id, "user": username}
            )
        )
        while True:  # 初期ダンプを受信し、追加が途切れたら(timeout)終了
            for line in ws.recv().split("\n"):
                if not line:
                    continue
                data = json.loads(line)
                if data.get("method") == "set":
                    name = str(data.get("name", "")).removeprefix("☁ ")
                    values[name] = str(data.get("value"))
    except (websocket.WebSocketException, OSError, json.JSONDecodeError):
        pass  # timeout含む受信終了
    finally:
        try:
            ws.close()
        except Exception:
            pass
    return values


class CloudReceiver:
    """送信用の接続をそのまま読み、届いた変数の更新を渡す。ping にも応答する。

    scratchattach の受信（`cloud.events()`）は使わない。理由は2つ。

    1. **自前サーバを指定しても、受信用の接続が公開サーバにつながる**。
       scratchattach 2.2.1 の `WebSocketEventStream` は、`TwCloud` を作り直すときに
       `cloud_host` を引き継がない（`CustomCloud` のサブクラスでないため）。
       そのため Render に切り替えた 2026-07-23 以降、Scratch からの回答
       （`S2P_ANSWER`）は**一度も Python に届いていなかった**（2026-10-04 に本番の
       Scratch プロジェクトとの結合で発見）。
    2. 送信用の接続は何も読まないと ping に応答できず、1〜2分ごとにサーバに切られ、
       直後の送信が黙って失われる（websocket-client は recv() したときにしか pong を返さない）。

    そこで送信用の接続を読み続け、ping に応答しつつ `set` を取り出す。
    接続した直後に届く「今の値のまとめ送り」は古い回答を含むので読み捨てる。
    scratchattach が再接続して接続が入れ替わったときも同じ扱いにする。
    """

    INITIAL_DUMP_SECONDS = 0.5

    def __init__(
        self,
        cloud: sa.TwCloud,
        on_set: Callable[[str, object], None],
        stop: threading.Event,
    ) -> None:
        self.cloud = cloud
        self.on_set = on_set
        self._stop = stop

    def start(self) -> None:
        threading.Thread(target=self._run, name="cloud-receiver", daemon=True).start()

    def handle_lines(self, raw: str, *, skip: bool) -> None:
        """受信した1フレーム（改行区切りで複数メッセージのことがある）を処理する。"""
        for line in str(raw).split("\n"):
            if not line or skip:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("method") != "set":
                continue
            name = str(data.get("name", "")).removeprefix("☁ ")
            self.on_set(name, data.get("value"))

    def _run(self) -> None:
        current = None
        skip_until = 0.0
        while not self._stop.is_set():
            ws = getattr(self.cloud, "websocket", None)
            sock = getattr(ws, "sock", None)
            if ws is None or sock is None or not ws.connected:
                self._stop.wait(1.0)  # 再接続中
                continue
            if ws is not current:  # 新しい接続（最初の接続・再接続）
                current = ws
                skip_until = time.monotonic() + self.INITIAL_DUMP_SECONDS
            try:
                # 読めるものが届いたときだけ recv する（フレームの途中で
                # タイムアウトして受信状態が壊れないように）
                readable, _, _ = select.select([sock], [], [], 1.0)
                if readable:
                    raw = ws.recv()  # ping ならこの中で pong を返す
                    self.handle_lines(raw, skip=time.monotonic() < skip_until)
            except (websocket.WebSocketException, OSError, ValueError):
                self._stop.wait(1.0)  # 切断時は scratchattach の再接続に任せる


class CloudBridge:
    """TurboWarp cloudへの接続と、プロトコル変数の読み書きを担当する。"""

    def __init__(
        self,
        project_id: str,
        *,
        cloud_host: str | None = None,
        on_answer: Callable[[int], None] | None = None,
        on_ack: Callable[[int], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.project_id = project_id
        self.cloud_host = resolve_cloud_host(cloud_host)
        self.on_answer = on_answer
        self.on_ack = on_ack
        self.on_error = on_error or (lambda msg: print(f"[cloud] {msg}"))
        self.cloud: sa.TwCloud | None = None
        self._receiver: CloudReceiver | None = None
        self._seq = 0
        self._last_snapshot: Snapshot | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---- 接続管理 ------------------------------------------------

    def connect(self) -> None:
        self.cloud = sa.get_tw_cloud(
            self.project_id,
            purpose="鈴鹿高専 高専祭ステージイベント「スズリーグ」の進行システム",
            contact="https://github.com/InoueKoshi",
            cloud_host=self.cloud_host,
        )
        self.cloud.connect()  # scratchattach は最初の送信まで接続しないので、先につないでおく
        self._receiver = CloudReceiver(self.cloud, self._handle_set, self._stop)
        self._receiver.start()
        self._start_heartbeat()

    def disconnect(self) -> None:
        self._stop.set()
        if self.cloud is not None:
            try:
                self.cloud.disconnect()
            except Exception:
                pass

    def _start_heartbeat(self) -> None:
        def beat() -> None:
            while not self._stop.wait(HEARTBEAT_INTERVAL):
                try:
                    self._set_var(protocol.VAR_HEARTBEAT, int(time.time()))
                except Exception as e:
                    self.on_error(f"heartbeat送信失敗: {e}")

        self._heartbeat_thread = threading.Thread(target=beat, daemon=True)
        self._heartbeat_thread.start()

    # ---- 送信 ----------------------------------------------------

    def push(self, snapshot: Snapshot) -> int:
        """エンジンの状態をP2S変数群として送信する。

        注意: scratchattachのset_vars()は複数JSONを1つのWebSocketフレームに
        まとめて送るが、TurboWarpのcloudサーバはこれを不正フレームとして
        無視し、以降その接続からの送信を全て破棄する（検証済み）。
        そのため必ず1変数ずつset_var()で送る。SEQは最後に送り、
        Scratch側がSEQ変化を検知した時点で他の変数が更新済みであることを保証する。
        """
        assert self.cloud is not None, "connect()前にpushされました"
        self._seq += 1
        self._last_snapshot = snapshot
        for name, value in protocol.encode_snapshot(snapshot, self._seq).items():
            self.cloud.set_var(name, value)
        return self._seq

    def resync(self) -> int | None:
        """最後に送った状態を再送する（Scratch側のリロード後などに使う）。"""
        if self._last_snapshot is None:
            return None
        return self.push(self._last_snapshot)

    def _set_var(self, name: str, value: int) -> None:
        assert self.cloud is not None
        self.cloud.set_var(name, value)

    # ---- 受信 ----------------------------------------------------

    def _handle_set(self, name: str, value: object) -> None:
        """S2P変数の更新イベントを処理する（イベントスレッドで呼ばれる）。"""
        if name == protocol.VAR_S2P_ANSWER:
            if self.on_answer is None:
                return
            try:
                self.on_answer(protocol.parse_percent(value))
            except protocol.ProtocolError as e:
                self.on_error(f"不正な回答値を無視: {e}")
        elif name == protocol.VAR_S2P_ACK:
            if self.on_ack is None:
                return
            try:
                self.on_ack(int(float(str(value))))
            except (TypeError, ValueError):
                self.on_error(f"不正なACK値を無視: {value!r}")


def smoke_test(
    project_id: str,
    listen_seconds: float = 10.0,
    cloud_host: str | None = None,
) -> None:
    """疎通確認: 変数を書き込み→読み戻し→イベント受信を待つ。

    使い方: uv run python -m suzuleague.cloud [--project-id ID] [--cloud-host URL]
    別クライアント（TurboWarpで開いたScratch画面等）から
    ☁ S2P_ANSWER を書き換えると受信ログが出る。
    セルフホストのサーバに切り替えたときの動作確認にも使う。
    """
    received: list[tuple[str, object]] = []

    bridge = CloudBridge(
        project_id,
        cloud_host=cloud_host,
        on_answer=lambda pct: received.append(("answer", pct)),
        on_ack=lambda code: received.append(("ack", code)),
    )
    print(f"[1/3] cloudサーバへ接続中... (project_id={project_id})")
    print(f"      接続先: {bridge.cloud_host}")
    bridge.connect()
    print("      接続OK")

    print("[2/3] 変数書き込みテスト (HEARTBEAT)")
    now = int(time.time())
    bridge._set_var(protocol.VAR_HEARTBEAT, now)
    time.sleep(1)
    readback = fetch_all_vars(project_id, cloud_host=bridge.cloud_host).get(
        protocol.VAR_HEARTBEAT
    )
    print(f"      書き込み={now} 読み戻し={readback}")
    if str(readback) != str(now):
        print("      × 読み戻し値が一致しません")
    else:
        print("      ○ 一致")

    print(f"[3/3] {listen_seconds:.0f}秒間 S2P変数の受信を待機...")
    print("      （TurboWarp側で ☁ S2P_ANSWER を変更すると受信されます）")
    time.sleep(listen_seconds)
    if received:
        for kind, value in received:
            print(f"      受信: {kind} = {value}")
    else:
        print("      受信なし（相手側クライアントがなければ正常）")

    bridge.disconnect()
    print("完了")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="cloudサーバ疎通テスト")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--listen", type=float, default=10.0, help="受信待機秒数")
    parser.add_argument(
        "--cloud-host",
        default=None,
        help=f"接続先cloudサーバ (環境変数 {ENV_CLOUD_HOST} でも指定可。既定は公開サーバ)",
    )
    args = parser.parse_args()
    try:
        smoke_test(
            resolve_project_id(args.project_id), args.listen, cloud_host=args.cloud_host
        )
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()

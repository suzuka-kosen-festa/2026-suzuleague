"""cloud変数サーバの同時接続数を検証する負荷テスト。

観客スマホ参加（QRコード→TurboWarpのURL）では、観客の人数分だけ
cloudサーバへの接続が増える。何人まで捌けるのかを実測するためのツール。

scratchattachは1接続あたりのオーバーヘッドが大きく、イベントスレッドも
非デーモンで残るため、ここでは生のWebSocketで軽量なクライアントを並べる。

**重要**: 公開サーバ(clouddata.turbowarp.org)に対して多数の合成接続を張るのは
TurboWarpの利用方針（「botは1接続まで」「接続の高速な開閉は禁止」）に反する。
ボランティア運営の無料サービスなので、負荷検証は必ずローカルに立てた
cloud-server（https://github.com/TurboWarp/cloud-server）に対して行うこと。

使い方:
    # ローカルサーバに対して段階的に負荷をかける
    uv run python -m suzuleague.loadtest --host ws://localhost:9080 --ramp 10,50,100,130

    # 公開サーバへの疎通確認（少数のみ。負荷検証には使わない）
    uv run python -m suzuleague.loadtest --clients 3

    # 観客ランキング: 正解発表の直後に全員が一斉に成績を送る状況を再現する（自前サーバのみ）
    uv run python -m suzuleague.loadtest --host wss://suzuleague-cloud.onrender.com --ranking 150
"""

from __future__ import annotations

import argparse
import json
import ssl
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid

import websocket

from .cloud import TW_CLOUD_HOST, normalize_cloud_host

# TurboWarpのcloud-serverはユーザ名を検証する（英数と_-のみ、20文字以内）。
# 実際の観客も "player123456" 形式の自動生成名で繋ぐので、それに合わせる。
USERNAME_FORMAT = "player{:06d}"
PROBE_VAR = "LOADTEST_PROBE"


class Client:
    """cloudサーバに繋いで変数の更新を受信するだけの軽量クライアント。"""

    def __init__(self, index: int, project_id: str, host: str) -> None:
        self.index = index
        self.project_id = project_id
        self.host = host
        self.ws: websocket.WebSocket | None = None
        self.connect_error: str | None = None
        self.received: list[tuple[str, str, float]] = []  # (name, value, 受信時刻)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def connect(self, timeout: float = 10.0) -> bool:
        try:
            ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
            ws.connect(
                self.host,
                origin="https://turbowarp.org",
                timeout=timeout,
                header={
                    "User-Agent": "suzuleague-loadtest (https://github.com/suzuka-kosen-festa)"
                },
            )
            ws.send(
                json.dumps(
                    {
                        "method": "handshake",
                        "project_id": self.project_id,
                        "user": USERNAME_FORMAT.format(self.index),
                    }
                )
            )
            self.ws = ws
        except Exception as e:  # 接続失敗の理由を残す（上限に当たったのか等の判別用）
            self.connect_error = f"{type(e).__name__}: {e}"
            return False

        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        return True

    def _listen(self) -> None:
        assert self.ws is not None
        self.ws.settimeout(1.0)
        while not self._stop.is_set():
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            now = time.perf_counter()
            if not raw:
                break
            for line in raw.split("\n"):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("method") == "set":
                    name = str(data.get("name", "")).removeprefix("☁ ")
                    self.received.append((name, str(data.get("value")), now))

    def set_var(self, name: str, value: object) -> None:
        assert self.ws is not None
        self.ws.send(
            json.dumps(
                {
                    "method": "set",
                    "name": f"☁ {name}",
                    "value": str(value),
                    "project_id": self.project_id,
                    "user": USERNAME_FORMAT.format(self.index),
                }
            )
        )

    def close(self) -> None:
        self._stop.set()
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass


def run_round(
    n: int,
    *,
    project_id: str,
    host: str,
    connect_interval: float,
    settle: float,
) -> dict[str, object]:
    """n個のクライアントを繋ぎ、ブロードキャストが全員に届くかを測る。"""
    clients = [Client(i, project_id, host) for i in range(n)]
    connect_started = time.perf_counter()
    for c in clients:
        c.connect()
        if connect_interval:
            time.sleep(connect_interval)  # 接続の高速連打を避ける
    connect_elapsed = time.perf_counter() - connect_started

    connected = [c for c in clients if c.ws is not None]
    failures = [c for c in clients if c.ws is None]
    time.sleep(settle)  # 初期ダンプを受け切ってから計測に入る

    result: dict[str, object] = {
        "要求接続数": n,
        "接続成功": len(connected),
        "接続失敗": len(failures),
        "接続所要秒": round(connect_elapsed, 2),
        "失敗理由": sorted({c.connect_error or "" for c in failures})[:3],
    }

    if connected:
        # 1台が変数を更新し、他の全員に届くまでの時間を測る
        publisher = connected[0]
        listeners = connected[1:]
        for c in listeners:
            c.received.clear()
        marker = str(int(time.time() * 1000) % 100000)
        sent_at = time.perf_counter()
        try:
            publisher.set_var(PROBE_VAR, marker)
        except Exception as e:
            result["配信"] = f"送信失敗: {e}"
            for c in clients:
                c.close()
            return result
        time.sleep(settle)

        latencies: list[float] = []
        delivered = 0
        for c in listeners:
            for name, value, at in c.received:
                if name == PROBE_VAR and value == marker:
                    delivered += 1
                    latencies.append((at - sent_at) * 1000)
                    break
        result["配信到達"] = f"{delivered}/{len(listeners)}"
        if latencies:
            result["遅延ms(中央値)"] = round(statistics.median(latencies), 1)
            result["遅延ms(最大)"] = round(max(latencies), 1)

    for c in clients:
        c.close()
    return result


def _percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * p))]


def run_ranking_burst(n: int, base_url: str, timeout: float = 30.0) -> dict[str, object]:
    """n台が同時に成績を送り、続けて同時に順位を取りに行く。

    正解発表の直後は、観客全員の端末が一斉に /api/score を送る。
    そのときの遅延と失敗数を測る。送った成績は「負荷試験◯」という名前で残るので、
    本番サーバで試したあとは司会者画面の「ランキングをリセット」で消すこと。
    """
    barrier = threading.Barrier(n)
    run_id = uuid.uuid4().hex[:8]
    lock = threading.Lock()
    post_ms: list[float] = []
    get_ms: list[float] = []
    errors: list[str] = []

    def request(url: str, body: bytes | None) -> float:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST" if body is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout) as res:
            res.read()
        return (time.perf_counter() - start) * 1000

    def viewer(i: int) -> None:
        viewer_id = f"loadtest-{run_id}-{i:04d}"
        body = json.dumps(
            {
                "id": viewer_id,
                "name": f"負荷試験{i}",
                "error_sum": i % 100,
                "answered": 1,
                "last_question": 1,
            }
        ).encode()
        barrier.wait()
        try:
            ms = request(f"{base_url}/api/score", body)
            got = request(f"{base_url}/api/ranking?id={viewer_id}", None)
            with lock:
                post_ms.append(ms)
                get_ms.append(got)
        except (urllib.error.URLError, OSError) as e:
            with lock:
                errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=viewer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result: dict[str, object] = {"成功": f"{len(post_ms)}/{n}", "失敗": len(errors)}
    if post_ms:
        result["成績の送信 p50/p95/最大(ms)"] = (
            f"{_percentile(post_ms, 0.5):.0f} / {_percentile(post_ms, 0.95):.0f} / {max(post_ms):.0f}"
        )
        result["順位の取得 p50/p95/最大(ms)"] = (
            f"{_percentile(get_ms, 0.5):.0f} / {_percentile(get_ms, 0.95):.0f} / {max(get_ms):.0f}"
        )
    if errors:
        result["失敗の例"] = errors[:3]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="cloud変数サーバの同時接続数の負荷テスト"
    )
    parser.add_argument(
        "--host",
        "--cloud-host",
        dest="host",
        default="ws://localhost:9080",
        help="cloudサーバのURL（既定はローカル。公開サーバへの負荷検証は禁止）",
    )
    parser.add_argument("--project-id", default="suzuleague-loadtest")
    parser.add_argument("--clients", type=int, default=None, help="単発実行の接続数")
    parser.add_argument(
        "--ramp",
        default=None,
        help="段階実行の接続数をカンマ区切りで（例: 10,50,100,130）",
    )
    parser.add_argument(
        "--connect-interval",
        type=float,
        default=0.02,
        help="接続と接続の間隔（秒）。連打を避けるため既定で少し空ける",
    )
    parser.add_argument(
        "--settle", type=float, default=2.0, help="受信を待つ秒数"
    )
    parser.add_argument(
        "--cooldown", type=float, default=5.0, help="ラウンド間の待機秒数"
    )
    parser.add_argument(
        "--ranking",
        type=int,
        default=None,
        help="観客ランキングの一斉送信を指定台数で試す（cloud変数の接続数の検証は行わない）",
    )
    args = parser.parse_args()

    if args.ranking:
        if TW_CLOUD_HOST in normalize_cloud_host(args.host):
            parser.error("観客ランキングのAPIは自前のcloud-serverにしかありません")
        from .host import http_base_from_cloud_host

        base_url = http_base_from_cloud_host(normalize_cloud_host(args.host))
        print(f"対象サーバ: {base_url}  ({args.ranking}台が同時に成績を送信)")
        for key, value in run_ranking_burst(args.ranking, base_url).items():
            print(f"  {key}: {value}")
        return

    if args.ramp:
        steps = [int(x) for x in args.ramp.split(",")]
    else:
        steps = [args.clients or 10]

    try:
        args.host = normalize_cloud_host(args.host)
    except ValueError as e:
        parser.error(str(e))

    if TW_CLOUD_HOST in args.host and max(steps) > 5:
        parser.error(
            "公開サーバ(clouddata.turbowarp.org)に対する多数接続はTurboWarpの利用方針違反です。\n"
            "負荷検証はローカルのcloud-serverに対して行ってください:\n"
            "  git clone https://github.com/TurboWarp/cloud-server && npm install && npm start"
        )

    print(f"対象サーバ: {args.host}  (project_id={args.project_id})")
    for i, n in enumerate(steps):
        if i:
            time.sleep(args.cooldown)
        print(f"\n--- {n}接続 ---")
        result = run_round(
            n,
            project_id=args.project_id,
            host=args.host,
            connect_interval=args.connect_interval,
            settle=args.settle,
        )
        for key, value in result.items():
            if value not in ([], "", None):
                print(f"  {key}: {value}")


if __name__ == "__main__":
    main()

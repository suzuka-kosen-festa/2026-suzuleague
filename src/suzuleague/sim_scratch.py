"""Scratch側の振る舞いを模擬するシミュレータ。

Scratch担当の実装を待たずに、実際のTurboWarp cloud経由で
バックエンドのE2E検証をするためのツール。

使い方:
    uv run python -m suzuleague.sim_scratch            # 手動回答モード
    uv run python -m suzuleague.sim_scratch --auto     # 自動回答モード(乱数)
    uv run python -m suzuleague.sim_scratch --auto --fixed 50  # 自動(固定値)

ダッシュボードと別ターミナルで起動して使う。
P2S変数の更新を受信して「ステージ画面」を模擬表示し、
回答受付ステートになったら S2P_ANSWER を書き込む。
"""

from __future__ import annotations

import argparse
import random
import threading
import time

import scratchattach as sa

from . import protocol
from .cloud import (
    ENV_CLOUD_HOST,
    CloudReceiver,
    fetch_all_vars,
    resolve_cloud_host,
    resolve_project_id,
)
from .engine import State
from .questions import QuestionSet

ANSWERING_STATES = (State.ANSWERING, State.EXHIBITION_ANSWERING)


class ScratchSimulator:
    def __init__(
        self,
        project_id: str,
        *,
        cloud_host: str | None = None,
        auto: bool = False,
        fixed_answer: int | None = None,
        auto_delay: float = 1.0,
    ) -> None:
        self.project_id = project_id
        self.cloud_host = resolve_cloud_host(cloud_host)
        self.auto = auto
        self.fixed_answer = fixed_answer
        self.auto_delay = auto_delay
        self.questions = QuestionSet()  # Scratch側が持つ問題文リストの代わり
        self.cloud: sa.TwCloud | None = None
        self._stop = threading.Event()
        self._p2s: dict[str, str] = {}  # 受信したP2S変数のキャッシュ
        self._s2p_seq = 0
        self._lock = threading.Lock()

    # ---- 接続 ----------------------------------------------------

    def connect(self) -> None:
        # 起動前に送信済みの状態があれば初期ダンプから取り込む
        self._p2s = {
            k: v
            for k, v in fetch_all_vars(
                self.project_id, cloud_host=self.cloud_host
            ).items()
            if k.startswith("P2S_")
        }
        self.cloud = sa.get_tw_cloud(
            self.project_id,
            purpose="スズリーグ Scratch側シミュレータ（開発用）",
            contact="https://github.com/InoueKoshi",
            cloud_host=self.cloud_host,
        )
        self.cloud.connect()
        # scratchattach の受信は自前サーバに対応していないので、送信用の接続を読む（cloud.py 参照）
        CloudReceiver(self.cloud, lambda name, value: self._handle_set(name, str(value)), self._stop).start()
        if self._p2s:
            self._render()

    # ---- 受信 ----------------------------------------------------

    def _handle_set(self, name: str, value: str) -> None:
        if name == protocol.VAR_HEARTBEAT:
            return
        if not name.startswith("P2S_"):
            return
        with self._lock:
            self._p2s[name] = value
        # SEQは他の変数の後に届く（=1回のpushの完了通知）
        if name == protocol.VAR_P2S_SEQ:
            self._render()
            self._maybe_auto_answer()

    def _get_int(self, name: str, default: int = 0) -> int:
        try:
            return int(float(self._p2s.get(name, default)))
        except ValueError:
            return default

    @property
    def state(self) -> State:
        try:
            return State(self._get_int(protocol.VAR_P2S_STATE))
        except ValueError:
            return State.IDLE

    # ---- 「ステージ画面」の模擬表示 -------------------------------

    def _render(self) -> None:
        state = self.state
        team = self._get_int(protocol.VAR_P2S_TEAM)
        round_no = self._get_int(protocol.VAR_P2S_ROUND)
        qid = self._get_int(protocol.VAR_P2S_QID)
        correct = self._get_int(protocol.VAR_P2S_CORRECT, -1)
        balloons = self._get_int(protocol.VAR_P2S_BALLOONS)

        print(f"\n===== ステージ画面 (SEQ={self._get_int(protocol.VAR_P2S_SEQ)}) =====")
        if state is State.IDLE:
            print("  [まもなく開始します]")
        elif state is State.TEAM_INTRO:
            print(f"  ようこそ チーム{team}！ (バルーン {balloons})")
        elif state is State.QUESTION:
            print(f"  第{round_no}問: {self._question_text(qid)}")
        elif state in ANSWERING_STATES:
            label = "エキシビション" if state is State.EXHIBITION_ANSWERING else "シンキングタイム"
            print(f"  第{round_no}問: {self._question_text(qid)}")
            print(f"  ⏱  {label}！ 回答を入力してください (0-100)")
        elif state is State.REVEAL:
            print(f"  正解は・・・ {correct}% でした！ 🎈残り {balloons}")
        elif state is State.ROUND_RESULT:
            print(f"  第{round_no}問 終了 🎈バルーン残り {balloons}")
        elif state is State.TEAM_RESULT:
            result = "ゲームオーバー😢" if balloons <= 0 else f"クリア🎉 (バルーン{balloons})"
            print(f"  チーム{team} {result}")
        elif state is State.FINISHED:
            print("  ✨全チーム終了！表彰式✨")
        print("=" * 36)

    def _question_text(self, qid: int) -> str:
        try:
            return self.questions.by_id(qid).text
        except KeyError:
            return f"(問題ID {qid} が見つかりません)"

    # ---- 回答送信 -------------------------------------------------

    def send_answer(self, percent: int) -> None:
        # set_vars()はTurboWarpサーバに無視されるため1変数ずつ送る（cloud.py参照）
        assert self.cloud is not None
        self._s2p_seq += 1
        self.cloud.set_var(protocol.VAR_S2P_ANSWER, percent)
        self.cloud.set_var(protocol.VAR_S2P_SEQ, self._s2p_seq)
        print(f"  → 回答 {percent}% を送信しました")

    def send_ack(self, code: int = protocol.ACK_ANIMATION_DONE) -> None:
        assert self.cloud is not None
        self.cloud.set_var(protocol.VAR_S2P_ACK, code)
        print(f"  → ACK({code}) を送信しました")

    def _maybe_auto_answer(self) -> None:
        if not self.auto or self.state not in ANSWERING_STATES:
            return

        def answer_later() -> None:
            time.sleep(self.auto_delay)
            if self.state in ANSWERING_STATES:  # まだ回答受付中なら
                value = (
                    self.fixed_answer
                    if self.fixed_answer is not None
                    else random.randint(0, 100)
                )
                self.send_answer(value)

        threading.Thread(target=answer_later, daemon=True).start()

    # ---- メインループ（手動操作） ---------------------------------

    def run(self) -> None:
        mode = "自動回答" if self.auto else "手動回答"
        print(f"Scratchシミュレータ起動 ({mode}モード, project_id={self.project_id})")
        print(f"  接続先: {self.cloud_host}")
        print("入力: <0-100>=回答送信 / ack=演出完了通知 / quit=終了")
        while True:
            try:
                line = input().strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.lower() in ("quit", "exit"):
                break
            if line.lower() == "ack":
                self.send_ack()
                continue
            try:
                percent = int(line)
            except ValueError:
                print("  0-100の数値 / ack / quit のいずれかを入力してください")
                continue
            if not 0 <= percent <= 100:
                print("  回答は0-100で入力してください")
                continue
            self.send_answer(percent)
        self.disconnect()
        print("終了しました")

    def disconnect(self) -> None:
        self._stop.set()
        if self.cloud is not None:
            try:
                self.cloud.disconnect()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Scratch側シミュレータ")
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--auto", action="store_true", help="回答受付になったら自動回答する")
    parser.add_argument("--fixed", type=int, default=None, help="自動回答の固定値(省略時は乱数)")
    parser.add_argument("--delay", type=float, default=1.0, help="自動回答までの秒数")
    parser.add_argument(
        "--cloud-host",
        default=None,
        help=f"接続先cloudサーバ (環境変数 {ENV_CLOUD_HOST} でも指定可。既定は公開サーバ)",
    )
    args = parser.parse_args()

    try:
        sim = ScratchSimulator(
            resolve_project_id(args.project_id),
            cloud_host=args.cloud_host,
            auto=args.auto,
            fixed_answer=args.fixed,
            auto_delay=args.delay,
        )
    except ValueError as e:
        parser.error(str(e))
    sim.connect()
    sim.run()


if __name__ == "__main__":
    main()

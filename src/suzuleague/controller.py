"""進行操作の窓口。

CLIダッシュボード・司会者画面（スマホ）・Scratchからの回答など、
操作の入口が複数あるため、エンジンへの操作はすべてここを通す。
同じロックで直列化し、操作のたびに cloud へ状態を送る。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .cloud import CloudBridge
from .engine import GameEngine, GameError, Snapshot, State


# 本番で「次へ」を受け付ける最短の間隔（秒）。これより短い間隔で続けて届いた分は取り消す。
# Scratch は P2S_SEQ の変化を1コマ（約0.03秒）ごとに見ているだけなので、2つのステートが
# ほぼ同時に届くと後のほうしか処理せず、間のステート（正解発表など）の演出が飛ぶ
# （2026-10-12 の実機テストで発生し、手元で再現）。押し間違いで2段階進むのも防ぐ。
MIN_ADVANCE_INTERVAL = 1.0


class GameController:
    def __init__(
        self,
        engine: GameEngine,
        bridge: CloudBridge | None = None,
        on_push_error: Callable[[Exception], None] | None = None,
        on_change: Callable[[], None] | None = None,
        min_advance_interval: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.engine = engine
        self.bridge = bridge
        self.lock = threading.RLock()  # engineへのアクセス保護（イベントスレッド対策）
        self._on_push_error = on_push_error
        # 進行や回答が変わるたびに呼ぶ（状態の保存に使う。ロックを持ったまま呼ぶ）
        self.on_change = on_change
        self._min_advance_interval = min_advance_interval
        self._clock = clock
        self._last_advance: float | None = None

    def advance(self, expect_state: int | None = None) -> State:
        """進行を1段階進める。

        expect_state を渡すと、エンジンがそのステートのときだけ進める。
        司会者画面は表示中のステートを添えて送ってくるので、通信の遅れで
        「次へ」が二重に届いても2段階進んでしまうことがない。
        """
        with self.lock:
            if expect_state is not None and int(self.engine.state) != expect_state:
                raise GameError(
                    "画面の表示が古いため操作を取り消しました（もう進んでいます）"
                )
            now = self._clock()
            if (
                self._last_advance is not None
                and now - self._last_advance < self._min_advance_interval
            ):
                raise GameError(
                    "続けて押されたので取り消しました（少し待ってからもう一度押してください）"
                )
            state = self.engine.advance()
            self._last_advance = now
            self._changed()
        self.push_state()
        return state

    def answer(self, percent: int) -> None:
        """回答を受け付ける。確定は advance() の正解発表時。"""
        with self.lock:
            self.engine.submit_answer(percent)
            self._changed()
        self._push(lambda bridge, snapshot: bridge.push_answer(snapshot))

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    def resync(self) -> None:
        """最後に送った状態を Scratch へ送り直す（Scratch を開き直した後などに使う）。"""
        if self.bridge is None:
            raise GameError("オフラインのため送り直せません")
        try:
            self.bridge.resync()
        except Exception as e:  # 通信失敗は画面に出して司会に知らせる
            raise GameError(f"送り直せませんでした（{type(e).__name__}）") from e

    def push_state(self) -> None:
        self._push(lambda bridge, snapshot: bridge.push(snapshot))

    def _push(self, send: Callable[[CloudBridge, Snapshot], object]) -> None:
        if self.bridge is None:
            return
        try:
            with self.lock:
                snapshot = self.engine.snapshot()
            send(self.bridge, snapshot)
        except Exception as e:  # 通信失敗で進行を止めない（resyncで再送できる）
            if self._on_push_error:
                self._on_push_error(e)

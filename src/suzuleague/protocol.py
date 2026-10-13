"""Scratch⇔Python間のcloud変数プロトコル定義。

TurboWarp cloudは数値のみ扱えるため、値はすべて整数にする（送るときは cloud.py が
文字列に直す）。変数一覧と意味は docs/protocol.md を参照（Scratch担当との共有仕様）。

このモジュールは通信ライブラリに依存しない純粋なエンコード/デコード。
"""

from __future__ import annotations

from .engine import Snapshot

# Python → Scratch（Pythonのみが書く）
VAR_P2S_SEQ = "P2S_SEQ"  # 更新カウンタ。Scratchはこれを監視する
VAR_P2S_STATE = "P2S_STATE"  # 進行ステート (engine.State の値)
VAR_P2S_TEAM = "P2S_TEAM"  # 現在のチーム番号 (1-4, 未開始0)
VAR_P2S_ROUND = "P2S_ROUND"  # 現在のラウンド (1-5, 未開始0)
VAR_P2S_QID = "P2S_QID"  # 問題ID (出題前0)
VAR_P2S_CORRECT = "P2S_CORRECT"  # 正解% (非公開時は-1)
VAR_P2S_BALLOONS = "P2S_BALLOONS"  # 現在チームのバルーン残数
VAR_P2S_ANSWER = "P2S_ANSWER"  # 出演者の回答% (届いていなければ-1)。大画面用のScratch画面が読む
VAR_P2S_SCORES = "P2S_SCORES"  # 全チームの残りバルーン（encode_scores の形）。全体結果の得点表に使う
VAR_HEARTBEAT = "HEARTBEAT"  # Python生存確認 (epoch秒)

# Scratch → Python（Scratchのみが書く）
VAR_S2P_SEQ = "S2P_SEQ"  # Scratch側の送信カウンタ
VAR_S2P_ANSWER = "S2P_ANSWER"  # 回答% (0-100)
VAR_S2P_ACK = "S2P_ACK"  # 演出完了などの通知コード

# S2P_ACK の通知コード
ACK_ANIMATION_DONE = 1  # バルーン割れアニメーション完了


class ProtocolError(Exception):
    """不正なcloud変数値を受信した。"""


SCORE_DIGITS = 3  # 1チームぶんの桁数
SCORE_NOT_FINISHED = 999  # まだ5問を終えていないチーム


def encode_scores(scores: tuple[int, ...]) -> int:
    """全チームの残りバルーンを1つの数にまとめる（cloud変数は数値しか送れないため）。

    先頭に 1 を置き、チーム番号順に3桁ずつ並べる。5問を終えていないチームは 999。
    例: チーム1が38・チーム2が0・チーム3と4はまだ → 1038000999999
    先頭の 1 は、頭の 0 が落ちないようにするための目印。
    """
    digits = "".join(
        f"{SCORE_NOT_FINISHED if s < 0 else min(s, SCORE_NOT_FINISHED - 1):0{SCORE_DIGITS}d}"
        for s in scores
    )
    return int("1" + digits)


def encode_snapshot(snapshot: Snapshot, seq: int) -> dict[str, int]:
    """エンジンのスナップショットをcloud変数の辞書に変換する。

    P2S_SEQ を最後に置くことで、Scratch側がSEQ変化を検知した時点で
    他の変数が更新済みであることを期待できる（cloud.py がこの順に1つずつ送る）。
    """
    return {
        VAR_P2S_STATE: int(snapshot.state),
        VAR_P2S_TEAM: snapshot.team_no,
        VAR_P2S_ROUND: snapshot.round_no,
        VAR_P2S_QID: snapshot.question_id,
        VAR_P2S_CORRECT: snapshot.correct,
        VAR_P2S_BALLOONS: snapshot.balloons,
        VAR_P2S_ANSWER: snapshot.answer,
        VAR_P2S_SCORES: encode_scores(snapshot.scores),
        VAR_P2S_SEQ: seq,
    }


def parse_percent(value: object) -> int:
    """cloud変数から届いた回答値を検証して0-100の整数にする。

    cloudの値は文字列("45"や"45.0")で届くことがあるため寛容にパースする。
    """
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        raise ProtocolError(f"数値として解釈できません: {value!r}") from None
    if number != int(number):
        raise ProtocolError(f"整数のパーセント値ではありません: {value!r}")
    percent = int(number)
    if not 0 <= percent <= 100:
        raise ProtocolError(f"回答は0-100の範囲: {percent}")
    return percent

"""進行ステートの表示用ラベル（CLIと司会者画面で共通）。"""

from __future__ import annotations

from .engine import State
from .models import Team
from .questions import ROUNDS_PER_TEAM

STATE_LABELS = {
    State.IDLE: "待機",
    State.TEAM_INTRO: "チーム紹介",
    State.QUESTION: "出題",
    State.ANSWERING: "回答受付",
    State.REVEAL: "正解発表",
    State.ROUND_RESULT: "ラウンド結果",
    State.TEAM_RESULT: "チーム結果",
    State.EXHIBITION_ANSWERING: "回答受付(エキシビション)",
    State.FINISHED: "全体結果",
}

# 各ステートで「next」が何をするかの案内
NEXT_HINTS = {
    State.IDLE: "next でチーム登場",
    State.TEAM_INTRO: "next で出題へ",
    State.QUESTION: "next で回答受付開始（シンキングタイム）",
    State.ANSWERING: "回答を受けてから next で正解発表",
    State.EXHIBITION_ANSWERING: "回答を受けてから next で正解発表",
    State.REVEAL: "next でラウンド結果へ",
    State.ROUND_RESULT: "next で次の問題（5問目終了後はチーム結果）へ",
    State.TEAM_RESULT: "next で次のチーム（最終チーム後は全体結果）へ",
    State.FINISHED: "ゲーム終了",
}


def team_status_label(team: Team) -> str:
    """チームの挑戦状況（司会者画面の「全チームの状況」とCLIの teams で共通）。"""
    if team.finished_rounds == 0:
        return "未挑戦"
    if team.is_failed:
        return "ゲームオーバー"
    if team.finished_rounds < ROUNDS_PER_TEAM:
        return "挑戦中"
    return "クリア"

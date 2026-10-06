"""ゲーム進行のステートマシン。

通信(cloud)やUIに依存しない純ロジック。司会ダッシュボードの「next」で
1段階ずつ進行し、回答は submit_answer() で受け付ける。

状態コードは docs/protocol.md の P2S_STATE と一致させている。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any

from .models import Question, RoundResult, Team
from .questions import ROUNDS_PER_TEAM, QuestionSet


class State(IntEnum):
    """進行ステート。値はそのまま ☁P2S_STATE として送信される。"""

    IDLE = 0  # 待機（イベント開始前）
    TEAM_INTRO = 1  # チーム登場・インタビュー
    QUESTION = 2  # 出題（問題文表示・読み上げ）
    ANSWERING = 3  # 回答受付（シンキングタイム）
    REVEAL = 4  # 正解発表（バルーンが割れるアニメーション）
    ROUND_RESULT = 5  # ラウンド結果表示
    TEAM_RESULT = 6  # チーム結果（クリア/ゲームオーバー）
    EXHIBITION_ANSWERING = 7  # エキシビション回答受付（採点なし）
    FINISHED = 8  # 全チーム終了・全体結果


class GameError(Exception):
    """不正な操作（順序違反・不正入力）。"""


@dataclass(frozen=True)
class Snapshot:
    """cloud送信・画面表示用の現在状態のスナップショット。"""

    state: State
    team_no: int  # 現在のチーム番号 (未開始時は0)
    round_no: int  # 現在のラウンド (未開始時は0)
    question_id: int  # 現在の問題ID (出題前は0)
    correct: int  # 正解% (REVEAL以降のみ有効、それ以外は-1)
    balloons: int  # 現在チームのバルーン残数


class GameEngine:
    """スズリーグ1回分のゲーム進行を管理する。

    進行は基本的に advance() で1段階ずつ進む。
    回答受付中のみ submit_answer() が有効。
    """

    def __init__(
        self,
        teams: list[Team] | None = None,
        question_set: QuestionSet | None = None,
        perfect_bonus: int = 0,  # ぴったり賞のボーナス。本番は不採用のため既定0のまま使う
        initial_balloons: int = 100,
    ) -> None:
        self.question_set = question_set or QuestionSet()
        if teams is None:
            teams = [Team(number=i, name=f"チーム{i}") for i in range(1, 5)]
        if not teams:
            raise ValueError("チームが1つ以上必要です")
        for team in teams:
            team.balloons = initial_balloons
        self.teams = teams
        self.perfect_bonus = perfect_bonus

        self.state: State = State.IDLE
        self._team_idx = -1  # teams のインデックス（未開始は-1）
        self._round_no = 0  # 1-5（未開始は0）
        self._pending_answer: int | None = None
        self._last_result: RoundResult | None = None

    # ---- 参照系 -------------------------------------------------

    @property
    def current_team(self) -> Team | None:
        if self._team_idx < 0:
            return None
        return self.teams[self._team_idx]

    @property
    def current_question(self) -> Question | None:
        team = self.current_team
        if team is None or self._round_no == 0:
            return None
        return self.question_set.for_team(team.number)[self._round_no - 1]

    @property
    def last_result(self) -> RoundResult | None:
        return self._last_result

    @property
    def pending_answer(self) -> int | None:
        """回答受付中に届いている回答（未確定）。届いていなければNone。"""
        return self._pending_answer

    @property
    def in_exhibition(self) -> bool:
        """現在チームがゲームオーバー済みで、残りをエキシビションで行う状態か。"""
        team = self.current_team
        return team is not None and team.is_failed

    def snapshot(self) -> Snapshot:
        team = self.current_team
        q = self.current_question
        reveal_states = (State.REVEAL, State.ROUND_RESULT)
        return Snapshot(
            state=self.state,
            team_no=team.number if team else 0,
            round_no=self._round_no,
            question_id=q.id if q else 0,
            correct=q.correct if (q and self.state in reveal_states) else -1,
            balloons=team.balloons if team else 0,
        )

    def winner(self) -> Team | None:
        """クリアしたチームの中で最多バルーンのチーム。全滅ならNone。"""
        cleared = [t for t in self.teams if t.finished_rounds > 0 and not t.is_failed]
        if not cleared:
            return None
        return max(cleared, key=lambda t: t.balloons)

    # ---- 操作系 -------------------------------------------------

    def submit_answer(self, percent: int) -> None:
        """回答を受け付ける（回答受付ステート中のみ有効）。

        Scratch側/CLIのどちらから来ても同じ扱い。確定は advance() の
        正解発表時に行うため、受付中は上書き可能。
        """
        if self.state not in (State.ANSWERING, State.EXHIBITION_ANSWERING):
            raise GameError(f"回答受付中ではありません (state={self.state.name})")
        if not 0 <= percent <= 100:
            raise GameError(f"回答は0-100のパーセント値: {percent}")
        self._pending_answer = int(percent)

    def advance(self) -> State:
        """進行を1段階進め、遷移後のステートを返す。"""
        handler = {
            State.IDLE: self._from_idle,
            State.TEAM_INTRO: self._from_team_intro,
            State.QUESTION: self._from_question,
            State.ANSWERING: self._reveal,
            State.EXHIBITION_ANSWERING: self._reveal,
            State.REVEAL: self._from_reveal,
            State.ROUND_RESULT: self._from_round_result,
            State.TEAM_RESULT: self._from_team_result,
        }.get(self.state)
        if handler is None:
            raise GameError("ゲームは終了しています")
        handler()
        return self.state

    # ---- 内部遷移 -----------------------------------------------

    def _from_idle(self) -> None:
        self._next_team()

    def _next_team(self) -> None:
        self._team_idx += 1
        self._round_no = 0
        self._pending_answer = None
        self._last_result = None
        self.state = State.TEAM_INTRO

    def _from_team_intro(self) -> None:
        self._round_no = 1
        self.state = State.QUESTION

    def _from_question(self) -> None:
        self._pending_answer = None
        self.state = (
            State.EXHIBITION_ANSWERING if self.in_exhibition else State.ANSWERING
        )

    def _reveal(self) -> None:
        """回答を確定して正解発表へ。ここで採点する。"""
        if self._pending_answer is None:
            raise GameError("まだ回答がありません")
        team = self.current_team
        question = self.current_question
        assert team is not None and question is not None

        exhibition = self.state is State.EXHIBITION_ANSWERING
        answer = self._pending_answer
        damage = abs(question.correct - answer)

        if exhibition:
            damage = 0  # エキシビションは採点なし
        else:
            team.balloons = max(0, team.balloons - damage)
            if damage == 0 and self.perfect_bonus:
                team.balloons += self.perfect_bonus  # ぴったり賞

        self._last_result = RoundResult(
            round_no=self._round_no,
            question_id=question.id,
            answer=answer,
            correct=question.correct,
            damage=damage,
            balloons_after=team.balloons,
            exhibition=exhibition,
        )
        team.results.append(self._last_result)
        self._pending_answer = None
        self.state = State.REVEAL

    def _from_reveal(self) -> None:
        self.state = State.ROUND_RESULT

    def _from_round_result(self) -> None:
        if self._round_no >= ROUNDS_PER_TEAM:
            self.state = State.TEAM_RESULT
        else:
            self._round_no += 1
            self.state = State.QUESTION

    def _from_team_result(self) -> None:
        if self._team_idx >= len(self.teams) - 1:
            self.state = State.FINISHED
        else:
            self._next_team()

    # ---- 保存と復元 ---------------------------------------------
    # 進行の状態は裏方PCのメモリにしかないため、起動し直すと最初に戻ってしまう。
    # 操作のたびに書き出しておき、起動時に読み戻して途中から再開する（#51）。

    def export_state(self) -> dict[str, Any]:
        """進行の状態をJSONにできる形で返す。"""
        return {
            "state": int(self.state),
            "team_idx": self._team_idx,
            "round_no": self._round_no,
            "pending_answer": self._pending_answer,
            "teams": [
                {
                    "number": t.number,
                    "name": t.name,
                    "balloons": t.balloons,
                    "results": [asdict(r) for r in t.results],
                }
                for t in self.teams
            ],
        }

    def restore_state(self, data: dict[str, Any]) -> None:
        """export_state() の内容を読み戻す。

        チーム構成や問題が保存時と違うと、別のゲームの続きを始めてしまうので
        ValueError にする。途中で失敗しても、エンジンの状態は変えない。
        """
        try:
            state = State(data["state"])
            team_idx = int(data["team_idx"])
            round_no = int(data["round_no"])
            pending = data["pending_answer"]
            saved_teams = list(data["teams"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"保存された進行の形式が正しくありません（{e}）") from e

        if [(t.get("number"), t.get("name")) for t in saved_teams] != [
            (t.number, t.name) for t in self.teams
        ]:
            raise ValueError("チーム構成が保存時と違います（teams.json を確かめてください）")
        if not -1 <= team_idx < len(self.teams):
            raise ValueError(f"チームの位置が正しくありません: {team_idx}")
        if not 0 <= round_no <= ROUNDS_PER_TEAM:
            raise ValueError(f"問題の番号が正しくありません: {round_no}")
        if pending is not None and not (isinstance(pending, int) and 0 <= pending <= 100):
            raise ValueError(f"届いていた回答が正しくありません: {pending!r}")

        restored: list[tuple[int, list[RoundResult]]] = []
        for team, saved in zip(self.teams, saved_teams, strict=True):
            questions = self.question_set.for_team(team.number)
            try:
                results = [RoundResult(**r) for r in saved["results"]]
                balloons = int(saved["balloons"])
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"チーム{team.number}の結果を読めません（{e}）") from e
            if len(results) > ROUNDS_PER_TEAM:
                raise ValueError(f"チーム{team.number}の結果が多すぎます")
            for i, r in enumerate(results):
                if r.round_no != i + 1 or r.question_id != questions[i].id:
                    raise ValueError("問題が保存時と違います（questions.py が変わった）")
            if results and balloons != results[-1].balloons_after:
                raise ValueError(f"チーム{team.number}の残りバルーンが結果と合いません")
            restored.append((balloons, results))

        for team, (balloons, results) in zip(self.teams, restored, strict=True):
            team.balloons = balloons
            team.results = results
        self.state = state
        self._team_idx = team_idx
        self._round_no = round_no
        self._pending_answer = pending
        # 直前の結果は「今のチームの最後の結果」と同じ（チームが替わると消える）
        team = self.current_team
        self._last_result = team.results[-1] if team and team.results else None


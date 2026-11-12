"""部門対抗モードの進行（振り返り会 2026-12-03）。

高専祭本番の GameEngine は「1チームずつ5問」だが、こちらは「全部門が同じ問題に同時に答える」。
進め方が違うので GameEngine は曲げずに別のクラスにした（docs/furikaeri/design.md）。

    待機 → 出題 → 回答受付 → 予想オープン → 正解発表 → 途中順位 → 出題 … → 最終順位

GameEngine と同じく、通信や画面を知らない純粋なロジック。状態の値は engine.State を使う
（司会者画面や保存ファイルで、同じ数字が同じ意味になるように）。

ルール:
- 各部門バルーン100から。正解発表のたびに |正解 − 予想| だけ減る（0 より下にはしない）
- 0 になった部門は脱落。そのあとも答えられるが減らない。誤差の合計だけ足し続ける
- 未回答は予想 50 とみなす（司会が「未回答のまま締め切る」で進めたとき、脱落した部門が答えなかったとき）
- 順位は残りバルーンの多い順。同じなら同率。0 の部門どうしは誤差の合計が小さい順
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .engine import GameError, State
from .models import Question, Team

INITIAL_BALLOONS = 100
UNANSWERED_GUESS = 50  # 未回答の部門は、この値を予想したとみなす


@dataclass(frozen=True)
class BattleResult:
    """1部門・1問の結果。"""

    round_no: int  # 1始まり
    question_id: int
    answer: int | None  # 部門が送った予想。未回答なら None
    correct: int
    error: int  # |正解 − 予想|（未回答は予想50として計算）
    damage: int  # 減ったバルーン（脱落済みなら0）
    balloons_after: int

    @property
    def guess(self) -> int:
        """採点に使った予想（未回答なら50）。"""
        return UNANSWERED_GUESS if self.answer is None else self.answer


@dataclass
class BattleTeam:
    number: int
    name: str
    active: bool = True  # 参加するか（待機中だけ切り替えられる）
    balloons: int = INITIAL_BALLOONS
    error_total: int = 0
    results: list[BattleResult] = field(default_factory=list)

    @property
    def eliminated(self) -> bool:
        return self.balloons <= 0


@dataclass(frozen=True)
class Standing:
    rank: int  # 同率なら同じ数（1位・1位・3位）
    team: BattleTeam


ADVANCE_FROM = (
    State.IDLE,
    State.QUESTION,
    State.ANSWERING,
    State.OPEN,
    State.REVEAL,
    State.ROUND_RESULT,
)


class BattleEngine:
    """全部門が同じ問題に同時に答える進行。advance() で1段階ずつ進む。"""

    def __init__(
        self,
        teams: list[Team],
        questions: list[Question],
        *,
        initial_balloons: int = INITIAL_BALLOONS,
        rehearsal: bool = False,
    ) -> None:
        if not teams:
            raise ValueError("部門が1つ以上必要です")
        if not questions:
            raise ValueError("問題が1つ以上必要です")
        numbers = [t.number for t in teams]
        if len(set(numbers)) != len(numbers):
            raise ValueError("部門の番号が重複しています")
        if len({q.id for q in questions}) != len(questions):
            raise ValueError("問題IDが重複しています")
        self.teams = [BattleTeam(t.number, t.name, balloons=initial_balloons) for t in teams]
        self.questions = list(questions)
        self.initial_balloons = initial_balloons
        self.is_rehearsal = rehearsal
        self.state: State = State.IDLE
        self.round_no = 0  # 1始まり（未開始は0）
        self.answers: dict[int, int] = {}  # 今の問題に届いている予想（部門番号 → 値）

    # ---- 参照系 -------------------------------------------------

    @property
    def rounds(self) -> int:
        return len(self.questions)

    @property
    def current_question(self) -> Question | None:
        if self.round_no == 0:
            return None
        return self.questions[self.round_no - 1]

    @property
    def active_teams(self) -> list[BattleTeam]:
        return [t for t in self.teams if t.active]

    def team(self, number: int) -> BattleTeam:
        for t in self.teams:
            if t.number == number:
                return t
        raise GameError(f"部門{number}はありません")

    def missing_teams(self) -> list[BattleTeam]:
        """まだ答えていない部門のうち、答えるまで待つ部門（参加中で脱落していない）。"""
        return [
            t for t in self.active_teams if not t.eliminated and t.number not in self.answers
        ]

    def last_result(self, team: BattleTeam) -> BattleResult | None:
        """今の問題の結果（正解発表と途中順位のあいだ。最終順位では最後の問題）。"""
        if self.state not in (State.REVEAL, State.ROUND_RESULT, State.FINISHED):
            return None
        if team.results and team.results[-1].round_no == self.round_no:
            return team.results[-1]
        return None

    def standings(self) -> list[Standing]:
        """参加中の部門の順位。残りバルーンの多い順、0 の部門どうしは誤差の合計が小さい順。"""

        def key(t: BattleTeam) -> tuple[int, int]:
            return (-t.balloons, t.error_total if t.eliminated else 0)

        ordered = sorted(self.active_teams, key=lambda t: (key(t), t.number))
        standings: list[Standing] = []
        for i, t in enumerate(ordered):
            if i > 0 and key(t) == key(ordered[i - 1]):
                rank = standings[-1].rank
            else:
                rank = i + 1
            standings.append(Standing(rank, t))
        return standings

    def winners(self) -> list[BattleTeam]:
        """優勝した部門（1位がすべて）。最終順位まで進むまでは空。"""
        if self.state is not State.FINISHED:
            return []
        return [s.team for s in self.standings() if s.rank == 1]

    # ---- 操作系 -------------------------------------------------

    def set_active(self, team_no: int, active: bool) -> None:
        """部門の参加・不参加を切り替える。始まったあとは変えない（順位がずれるため）。"""
        if self.state is not State.IDLE:
            raise GameError("参加する部門は、1問目を出す前にしか変えられません")
        self.team(team_no).active = bool(active)

    def submit_answer(self, team_no: int, percent: int) -> None:
        """部門の予想を受け付ける。締め切るまでは何度でも上書きできる。"""
        if self.state is not State.ANSWERING:
            raise GameError("いまは回答を受け付けていません")
        team = self.team(team_no)
        if not team.active:
            raise GameError(f"{team.name}は参加していません")
        if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
            raise GameError("回答は0〜100の整数で入力してください")
        self.answers[team.number] = percent

    def advance(self) -> State:
        """進行を1段階進め、進んだ先の状態を返す。"""
        if self.state is State.IDLE:
            if not self.active_teams:
                raise GameError("参加する部門がありません")
            self._next_question()
        elif self.state is State.QUESTION:
            self.answers = {}
            self.state = State.ANSWERING
        elif self.state is State.ANSWERING:
            missing = self.missing_teams()
            if missing:
                names = "、".join(t.name for t in missing)
                raise GameError(f"まだ答えていない部門があります：{names}")
            self.state = State.OPEN
        elif self.state is State.OPEN:
            self._score()
            self.state = State.REVEAL
        elif self.state is State.REVEAL:
            self.state = State.ROUND_RESULT
        elif self.state is State.ROUND_RESULT:
            if self.round_no >= self.rounds:
                self.state = State.FINISHED
            else:
                self._next_question()
        else:
            raise GameError("ゲームは終了しています")
        return self.state

    def close(self) -> State:
        """未回答の部門を残したまま締め切り、予想オープンへ進む（答えない部門で止まらないように）。"""
        if self.state is not State.ANSWERING:
            raise GameError("締め切れるのは回答受付のあいだだけです")
        self.state = State.OPEN
        return self.state

    # ---- 内部 ---------------------------------------------------

    def _next_question(self) -> None:
        self.round_no += 1
        self.answers = {}
        self.state = State.QUESTION

    def _score(self) -> None:
        question = self.current_question
        assert question is not None
        for team in self.active_teams:
            answer = self.answers.get(team.number)
            guess = UNANSWERED_GUESS if answer is None else answer
            error = abs(question.correct - guess)
            damage = 0 if team.eliminated else min(error, team.balloons)
            team.balloons -= damage
            team.error_total += error
            team.results.append(
                BattleResult(
                    round_no=self.round_no,
                    question_id=question.id,
                    answer=answer,
                    correct=question.correct,
                    error=error,
                    damage=damage,
                    balloons_after=team.balloons,
                )
            )

    # ---- 保存と復元 ---------------------------------------------

    def export_state(self) -> dict[str, Any]:
        return {
            "mode": "battle",
            "state": int(self.state),
            "round_no": self.round_no,
            "answers": {str(k): v for k, v in self.answers.items()},
            "question_ids": [q.id for q in self.questions],
            "teams": [
                {
                    "number": t.number,
                    "name": t.name,
                    "active": t.active,
                    "balloons": t.balloons,
                    "error_total": t.error_total,
                    "results": [asdict(r) for r in t.results],
                }
                for t in self.teams
            ],
        }

    def restore_state(self, data: dict[str, Any]) -> None:
        """export_state() の内容を読み戻す。部門や問題が保存時と違えば ValueError（状態は変えない）。"""
        try:
            if data.get("mode") != "battle":
                raise ValueError("部門対抗モードの保存ではありません")
            state = State(data["state"])
            round_no = int(data["round_no"])
            answers = {int(k): v for k, v in dict(data["answers"]).items()}
            question_ids = list(data["question_ids"])
            saved_teams = list(data["teams"])
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ValueError(f"保存された進行の形式が正しくありません（{e}）") from e

        if state not in ADVANCE_FROM and state is not State.FINISHED:
            raise ValueError(f"部門対抗モードにない状態です: {state.name}")
        if question_ids != [q.id for q in self.questions]:
            raise ValueError("問題が保存時と違います（questions.py が変わった）")
        if [(t.get("number"), t.get("name")) for t in saved_teams] != [
            (t.number, t.name) for t in self.teams
        ]:
            raise ValueError("部門の構成が保存時と違います（teams-furikaeri.json を確かめてください）")
        if not 0 <= round_no <= self.rounds:
            raise ValueError(f"問題の番号が正しくありません: {round_no}")
        if (state is State.IDLE) != (round_no == 0):
            raise ValueError(f"状態と問題の番号が合いません: {state.name} / {round_no}")
        if not all(isinstance(v, int) and 0 <= v <= 100 for v in answers.values()):
            raise ValueError("届いていた回答が正しくありません")

        restored = []
        for saved in saved_teams:
            try:
                results = [BattleResult(**r) for r in saved["results"]]
                restored.append(
                    (bool(saved["active"]), int(saved["balloons"]), int(saved["error_total"]), results)
                )
            except (KeyError, TypeError, ValueError) as e:
                raise ValueError(f"部門{saved.get('number')}の結果を読めません（{e}）") from e

        for team, (active, balloons, error_total, results) in zip(self.teams, restored, strict=True):
            team.active = active
            team.balloons = balloons
            team.error_total = error_total
            team.results = results
        self.state = state
        self.round_no = round_no
        self.answers = answers

"""GameEngineの状態遷移・採点ロジックのテスト。"""

import pytest

from suzuleague.engine import GameEngine, GameError, State
from suzuleague.models import Question, Team
from suzuleague.questions import QuestionSet


def make_questions(corrects: list[int]) -> QuestionSet:
    """指定した正解%リスト（20問分）から問題セットを作る。"""
    qs = [Question(i + 1, f"Q{i + 1}", c) for i, c in enumerate(corrects)]
    return QuestionSet(qs)


def make_engine(corrects: list[int] | None = None, **kwargs) -> GameEngine:
    if corrects is None:
        corrects = [50] * 20  # 全問正解50%
    return GameEngine(question_set=make_questions(corrects), **kwargs)


def play_round(engine: GameEngine, answer: int) -> None:
    """QUESTION状態から1ラウンド回してROUND_RESULTまで進める。"""
    assert engine.state is State.QUESTION
    engine.advance()  # -> ANSWERING / EXHIBITION_ANSWERING
    engine.submit_answer(answer)
    engine.advance()  # -> REVEAL
    engine.advance()  # -> ROUND_RESULT


class TestBasicFlow:
    def test_initial_state(self):
        engine = make_engine()
        assert engine.state is State.IDLE
        assert engine.current_team is None
        assert engine.current_question is None

    def test_full_clear_flow(self):
        """1チームが5問すべて回答しクリアする一連の流れ。"""
        engine = make_engine()
        engine.advance()
        assert engine.state is State.TEAM_INTRO
        assert engine.current_team.number == 1

        engine.advance()
        assert engine.state is State.QUESTION
        assert engine.current_question.id == 1

        for round_no in range(1, 6):
            assert engine.snapshot().round_no == round_no
            play_round(engine, answer=40)  # 正解50 → ダメージ10
            engine.advance()  # ROUND_RESULT -> 次へ

        assert engine.state is State.TEAM_RESULT
        team = engine.current_team
        assert team.balloons == 100 - 10 * 5
        assert not team.is_failed
        assert team.finished_rounds == 5

    def test_four_teams_to_finish(self):
        engine = make_engine()
        for team_no in range(1, 5):
            engine.advance()  # -> TEAM_INTRO
            assert engine.current_team.number == team_no
            engine.advance()  # -> QUESTION
            for _ in range(5):
                play_round(engine, answer=50)
                engine.advance()
        assert engine.state is State.TEAM_RESULT
        engine.advance()  # 最終チームの結果 -> 全体結果
        assert engine.state is State.FINISHED
        with pytest.raises(GameError):
            engine.advance()

    def test_team_questions_assignment(self):
        """チーム2はID6-10の問題を出題される。"""
        engine = make_engine()
        # チーム1を消化
        engine.advance()
        engine.advance()
        for _ in range(5):
            play_round(engine, answer=50)
            engine.advance()
        # チーム2へ
        engine.advance()
        engine.advance()
        assert engine.current_question.id == 6


class TestScoring:
    def test_damage_is_absolute_difference(self):
        engine = make_engine(corrects=[70] + [50] * 19)
        engine.advance()
        engine.advance()
        play_round(engine, answer=45)
        result = engine.last_result
        assert result.damage == 25
        assert result.balloons_after == 75
        assert engine.current_team.balloons == 75

    def test_perfect_no_bonus_by_default(self):
        engine = make_engine()
        engine.advance()
        engine.advance()
        play_round(engine, answer=50)  # ぴったり
        assert engine.last_result.is_perfect
        assert engine.current_team.balloons == 100

    def test_perfect_bonus_enabled(self):
        engine = make_engine(perfect_bonus=10)
        engine.advance()
        engine.advance()
        play_round(engine, answer=50)  # ぴったり
        assert engine.current_team.balloons == 110

    def test_balloons_clamped_at_zero(self):
        """ダメージが残数を超えてもバルーンは0未満にならない。"""
        engine = make_engine(corrects=[100] * 20)
        engine.advance()
        engine.advance()
        play_round(engine, answer=0)  # ダメージ100 → 残0
        assert engine.current_team.balloons == 0
        assert engine.current_team.is_failed


class TestExhibition:
    def make_failed_engine(self) -> GameEngine:
        """1問目でゲームオーバーになった状態のエンジンを作る。"""
        engine = make_engine(corrects=[100] * 20)
        engine.advance()  # TEAM_INTRO
        engine.advance()  # QUESTION
        play_round(engine, answer=0)  # ダメージ100 → ゲームオーバー
        return engine

    def test_gameover_moves_to_exhibition(self):
        """ゲームオーバー後の残りラウンドはエキシビションになる。"""
        engine = self.make_failed_engine()
        engine.advance()  # ROUND_RESULT -> QUESTION (2問目)
        assert engine.state is State.QUESTION
        engine.advance()
        assert engine.state is State.EXHIBITION_ANSWERING

    def test_exhibition_has_no_scoring(self):
        engine = self.make_failed_engine()
        engine.advance()  # -> QUESTION
        play_round(engine, answer=0)  # 正解100と大外れだが…
        result = engine.last_result
        assert result.exhibition
        assert result.damage == 0
        assert engine.current_team.balloons == 0  # 変化しない

    def test_exhibition_until_team_end(self):
        """エキシビションでも5問目まで進み、チーム結果に到達する。"""
        engine = self.make_failed_engine()
        for _ in range(4):  # 残り2-5問目
            engine.advance()
            play_round(engine, answer=50)
            # play_roundはQUESTION前提なのでadvanceを内包しない
        assert engine.state is State.ROUND_RESULT
        engine.advance()
        assert engine.state is State.TEAM_RESULT
        assert engine.current_team.finished_rounds == 5

    def test_next_team_starts_fresh(self):
        """前チームがゲームオーバーでも次チームは通常モードで開始。"""
        engine = self.make_failed_engine()
        for _ in range(4):
            engine.advance()
            play_round(engine, answer=50)
        engine.advance()  # -> TEAM_RESULT
        engine.advance()  # -> 次チームTEAM_INTRO
        assert engine.current_team.number == 2
        assert not engine.in_exhibition
        engine.advance()  # -> QUESTION
        engine.advance()
        assert engine.state is State.ANSWERING


class TestValidation:
    def test_answer_outside_window_rejected(self):
        engine = make_engine()
        with pytest.raises(GameError):
            engine.submit_answer(50)
        engine.advance()  # TEAM_INTRO
        with pytest.raises(GameError):
            engine.submit_answer(50)

    def test_answer_range_validated(self):
        engine = make_engine()
        engine.advance()
        engine.advance()
        engine.advance()  # -> ANSWERING
        with pytest.raises(GameError):
            engine.submit_answer(101)
        with pytest.raises(GameError):
            engine.submit_answer(-1)

    def test_advance_without_answer_rejected(self):
        engine = make_engine()
        engine.advance()
        engine.advance()
        engine.advance()  # -> ANSWERING
        with pytest.raises(GameError):
            engine.advance()  # 回答なしで正解発表は不可

    def test_answer_can_be_overwritten(self):
        engine = make_engine()
        engine.advance()
        engine.advance()
        engine.advance()  # -> ANSWERING
        engine.submit_answer(10)
        engine.submit_answer(45)  # 上書き
        engine.advance()  # -> REVEAL
        assert engine.last_result.answer == 45


class TestSnapshotAndWinner:
    def test_correct_stays_through_team_result(self):
        """チーム結果でも最後の問題の正解を出す（ステージ画面を開き直しても組み立てられるように）。"""
        engine = make_engine([50] * 4 + [27] + [50] * 15)
        engine.advance()
        engine.advance()
        for _ in range(5):
            play_round(engine, answer=40)
            engine.advance()
        assert engine.state is State.TEAM_RESULT
        assert engine.snapshot().correct == 27
        engine.advance()  # 次のチーム
        assert engine.snapshot().correct == -1

    def test_correct_hidden_until_reveal(self):
        """正解%は正解発表より前では-1（隠蔽）。"""
        engine = make_engine()
        engine.advance()
        engine.advance()  # QUESTION
        assert engine.snapshot().correct == -1
        engine.advance()  # ANSWERING
        assert engine.snapshot().correct == -1
        engine.submit_answer(30)
        engine.advance()  # REVEAL
        assert engine.snapshot().correct == 50
        engine.advance()  # ROUND_RESULT
        assert engine.snapshot().correct == 50
        engine.advance()  # 次のQUESTION
        assert engine.snapshot().correct == -1

    def test_answer_is_shared_from_arrival_to_team_result(self):
        """出演者の回答は届いたときから出し、次の問題で-1に戻す（大画面用の画面が読む）。"""
        engine = make_engine()
        engine.advance()
        engine.advance()  # QUESTION
        for round_no in range(1, 6):
            assert engine.snapshot().answer == -1
            engine.advance()  # ANSWERING
            assert engine.snapshot().answer == -1
            engine.submit_answer(30 + round_no)
            assert engine.snapshot().answer == 30 + round_no
            engine.advance()  # REVEAL
            assert engine.snapshot().answer == 30 + round_no
            engine.advance()  # ROUND_RESULT
            assert engine.snapshot().answer == 30 + round_no
            engine.advance()  # 次のQUESTION / TEAM_RESULT
        assert engine.state is State.TEAM_RESULT
        assert engine.snapshot().answer == 35
        engine.advance()  # 次のチームのTEAM_INTRO
        assert engine.snapshot().answer == -1

    def test_winner_is_max_balloons_among_cleared(self):
        engine = make_engine()
        # チーム1: 毎回10ダメージ → 残50
        engine.advance()
        engine.advance()
        for _ in range(5):
            play_round(engine, answer=40)
            engine.advance()
        # チーム2: ノーダメージ → 残100
        engine.advance()
        engine.advance()
        for _ in range(5):
            play_round(engine, answer=50)
            engine.advance()
        # チーム3, 4: 毎回20ダメージ → 残0でゲームオーバー
        for _ in range(2):
            engine.advance()
            engine.advance()
            for _ in range(5):
                play_round(engine, answer=30)
                engine.advance()
        engine.advance()  # 最終チームの結果 -> 全体結果
        assert engine.state is State.FINISHED
        winner = engine.winner()
        assert winner is not None
        assert winner.number == 2

    def test_winner_none_when_all_failed(self):
        engine = make_engine(corrects=[100] * 20)
        for _ in range(4):
            engine.advance()
            engine.advance()
            for _ in range(5):
                play_round(engine, answer=0)
                engine.advance()
        assert engine.winner() is None


class TestTeamSetup:
    def test_custom_teams_reset_balloons(self):
        teams = [Team(number=1, name="A", balloons=42)]
        engine = GameEngine(teams=teams, question_set=make_questions([50] * 20))
        assert teams[0].balloons == 100
        engine.advance()
        assert engine.current_team.name == "A"

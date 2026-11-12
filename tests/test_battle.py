"""部門対抗モード（BattleEngine）の進行・採点・順位のテスト。"""

import pytest

from suzuleague.battle import BattleEngine
from suzuleague.engine import GameError, State
from suzuleague.models import Question, Team
from suzuleague.questions import (
    FURIKAERI_QUESTIONS,
    FURIKAERI_REHEARSAL_QUESTIONS,
    FURIKAERI_ROUNDS,
    FURIKAERI_SPARE_QUESTIONS,
    PRODUCTION_QUESTIONS,
    REHEARSAL_QUESTIONS,
    SPARE_QUESTIONS,
)
from suzuleague.teams import load_teams


def make_engine(corrects=(50, 50, 50), n_teams=3) -> BattleEngine:
    teams = [Team(i, f"部門{i}") for i in range(1, n_teams + 1)]
    questions = [Question(200 + i, f"Q{i}", c) for i, c in enumerate(corrects, start=1)]
    return BattleEngine(teams, questions)


def to_answering(engine: BattleEngine) -> None:
    if engine.state is State.IDLE:
        engine.advance()
    assert engine.state is State.QUESTION
    engine.advance()
    assert engine.state is State.ANSWERING


def play(engine: BattleEngine, answers: dict[int, int]) -> None:
    """出題から途中順位まで1問進める。"""
    to_answering(engine)
    for team_no, value in answers.items():
        engine.submit_answer(team_no, value)
    if engine.missing_teams():
        engine.close()
    else:
        engine.advance()
    assert engine.state is State.OPEN
    engine.advance()
    assert engine.state is State.REVEAL
    engine.advance()
    assert engine.state is State.ROUND_RESULT


class TestFlow:
    def test_states_in_order(self):
        engine = make_engine(corrects=(50,))
        seen = [engine.state]
        engine.advance()
        seen.append(engine.state)
        engine.advance()
        seen.append(engine.state)
        for n in (1, 2, 3):
            engine.submit_answer(n, 40)
        for _ in range(4):
            engine.advance()
            seen.append(engine.state)
        assert seen == [
            State.IDLE,
            State.QUESTION,
            State.ANSWERING,
            State.OPEN,
            State.REVEAL,
            State.ROUND_RESULT,
            State.FINISHED,
        ]
        with pytest.raises(GameError):
            engine.advance()

    def test_round_result_goes_to_next_question(self):
        engine = make_engine()
        play(engine, {1: 50, 2: 50, 3: 50})
        engine.advance()
        assert engine.state is State.QUESTION
        assert engine.round_no == 2
        assert engine.current_question.id == 202

    def test_cannot_open_until_everyone_answers(self):
        engine = make_engine()
        to_answering(engine)
        engine.submit_answer(1, 30)
        with pytest.raises(GameError, match="部門2、部門3"):
            engine.advance()
        assert engine.state is State.ANSWERING

    def test_close_skips_missing(self):
        engine = make_engine(corrects=(80,))
        to_answering(engine)
        engine.submit_answer(1, 80)
        engine.close()
        engine.advance()
        result = engine.last_result(engine.team(2))
        assert result.answer is None
        assert result.guess == 50
        assert result.error == 30
        assert engine.team(2).balloons == 70

    def test_close_only_while_answering(self):
        engine = make_engine()
        with pytest.raises(GameError):
            engine.close()

    def test_answers_reset_each_question(self):
        engine = make_engine()
        play(engine, {1: 10, 2: 20, 3: 30})
        engine.advance()
        engine.advance()
        assert engine.answers == {}

    def test_answer_only_while_answering(self):
        engine = make_engine()
        engine.advance()  # 出題中（読み上げ）はまだ受け付けない
        with pytest.raises(GameError):
            engine.submit_answer(1, 50)

    def test_answer_can_be_overwritten(self):
        engine = make_engine()
        to_answering(engine)
        engine.submit_answer(1, 10)
        engine.submit_answer(1, 60)
        assert engine.answers[1] == 60

    @pytest.mark.parametrize("value", [-1, 101, 50.0, True, "50"])
    def test_invalid_answer(self, value):
        engine = make_engine()
        to_answering(engine)
        with pytest.raises(GameError):
            engine.submit_answer(1, value)

    def test_unknown_team(self):
        engine = make_engine()
        to_answering(engine)
        with pytest.raises(GameError):
            engine.submit_answer(9, 50)


class TestActive:
    def test_inactive_team_is_ignored(self):
        engine = make_engine(corrects=(50,))
        engine.set_active(3, False)
        to_answering(engine)
        with pytest.raises(GameError):
            engine.submit_answer(3, 50)
        engine.submit_answer(1, 50)
        engine.submit_answer(2, 50)
        engine.advance()  # 部門3を待たずに予想オープンへ
        engine.advance()
        assert engine.team(3).results == []
        assert [s.team.number for s in engine.standings()] == [1, 2]

    def test_cannot_change_after_start(self):
        engine = make_engine()
        engine.advance()
        with pytest.raises(GameError):
            engine.set_active(1, False)

    def test_needs_one_team(self):
        engine = make_engine(n_teams=1)
        engine.set_active(1, False)
        with pytest.raises(GameError):
            engine.advance()


class TestScoring:
    def test_damage_is_error(self):
        engine = make_engine(corrects=(56,))
        play(engine, {1: 56, 2: 40, 3: 90})
        assert [engine.team(n).balloons for n in (1, 2, 3)] == [100, 84, 66]
        assert engine.team(3).results[0].damage == 34

    def test_balloons_floor_at_zero_and_error_keeps_counting(self):
        engine = make_engine(corrects=(0, 100, 0))
        play(engine, {1: 100, 2: 0, 3: 0})  # 部門1は100減って脱落
        team = engine.team(1)
        assert team.balloons == 0 and team.eliminated
        engine.advance()
        play(engine, {1: 0, 2: 100, 3: 100})  # 脱落後は減らないが誤差は足す
        assert team.results[-1].damage == 0
        assert team.results[-1].error == 100
        assert team.error_total == 200

    def test_damage_capped_by_remaining(self):
        engine = make_engine(corrects=(0, 100))
        play(engine, {1: 70, 2: 0, 3: 0})
        engine.advance()
        play(engine, {1: 40, 2: 100, 3: 100})  # 残り30に誤差60
        result = engine.team(1).results[-1]
        assert result.damage == 30
        assert result.error == 60
        assert result.balloons_after == 0

    def test_eliminated_team_need_not_answer(self):
        engine = make_engine(corrects=(0, 30))
        play(engine, {1: 100, 2: 0, 3: 0})
        engine.advance()
        to_answering(engine)
        engine.submit_answer(2, 30)
        engine.submit_answer(3, 30)
        assert engine.missing_teams() == []
        engine.advance()  # 脱落した部門1を待たない
        engine.advance()
        assert engine.team(1).results[-1].answer is None
        assert engine.team(1).results[-1].error == 20  # 予想50とみなす


class TestStandings:
    def test_ranked_by_balloons_with_ties(self):
        engine = make_engine(corrects=(50,), n_teams=4)
        play(engine, {1: 40, 2: 50, 3: 40, 4: 70})
        assert [(s.rank, s.team.number) for s in engine.standings()] == [
            (1, 2),
            (2, 1),
            (2, 3),
            (4, 4),
        ]

    def test_eliminated_ranked_by_error_total(self):
        engine = make_engine(corrects=(0, 0), n_teams=3)
        play(engine, {1: 100, 2: 100, 3: 10})
        engine.advance()
        play(engine, {1: 50, 2: 0, 3: 0})
        # 部門1・2は0で脱落。誤差合計は 150 と 100
        assert [(s.rank, s.team.number) for s in engine.standings()] == [(1, 3), (2, 2), (3, 1)]

    def test_winners_only_when_finished(self):
        engine = make_engine(corrects=(50,))
        play(engine, {1: 50, 2: 50, 3: 0})
        assert engine.winners() == []
        engine.advance()
        assert [t.number for t in engine.winners()] == [1, 2]

    def test_all_eliminated_lowest_error_wins(self):
        engine = make_engine(corrects=(0,))
        play(engine, {1: 100, 2: 100, 3: 100})
        engine.advance()
        assert [t.number for t in engine.winners()] == [1, 2, 3]  # 誤差も同じなら同率


class TestSaveRestore:
    def test_round_trip(self):
        engine = make_engine(corrects=(30, 60, 90))
        engine.set_active(3, False)
        play(engine, {1: 20, 2: 50})
        engine.advance()
        engine.advance()
        engine.submit_answer(1, 77)
        data = engine.export_state()

        other = make_engine(corrects=(30, 60, 90))
        other.restore_state(data)
        assert other.state is State.ANSWERING
        assert other.round_no == 2
        assert other.answers == {1: 77}
        assert other.team(3).active is False
        assert other.team(1).balloons == 90
        assert other.team(1).results == engine.team(1).results

    def test_rejects_different_questions(self):
        data = make_engine(corrects=(30, 60, 90)).export_state()
        with pytest.raises(ValueError, match="問題"):
            make_engine(corrects=(30, 60)).restore_state(data)

    def test_rejects_different_teams(self):
        data = make_engine(n_teams=3).export_state()
        with pytest.raises(ValueError, match="部門の構成"):
            make_engine(n_teams=4).restore_state(data)

    def test_rejects_normal_mode_save(self):
        with pytest.raises(ValueError):
            make_engine().restore_state({"state": 0, "team_idx": -1})


class TestQuestions:
    def test_six_questions(self):
        assert len(FURIKAERI_QUESTIONS) == FURIKAERI_ROUNDS == 6

    def test_ids_do_not_collide(self):
        groups = [
            PRODUCTION_QUESTIONS,
            REHEARSAL_QUESTIONS,
            SPARE_QUESTIONS,
            FURIKAERI_QUESTIONS,
            FURIKAERI_SPARE_QUESTIONS,
            FURIKAERI_REHEARSAL_QUESTIONS,
        ]
        ids = [q.id for g in groups for q in g]
        assert len(ids) == len(set(ids))

    def test_teams_file(self):
        teams = load_teams("teams-furikaeri.json")
        assert len(teams) == 10
        assert teams[0].name == "システム"

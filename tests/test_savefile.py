"""進行の保存と途中からの再開（#51）のテスト。"""

from __future__ import annotations

import json
import random

import pytest

from suzuleague import savefile
from suzuleague.controller import GameController
from suzuleague.dashboard import Dashboard, setup_save
from suzuleague.engine import GameEngine, State
from suzuleague.host import TeamCodes, build_host_state
from suzuleague.models import Team


def fixed_codes(engine: GameEngine, seed: int = 0) -> TeamCodes:
    rng = random.Random(seed)
    return TeamCodes([t.number for t in engine.teams], randbelow=rng.randrange)


def play_steps(seed: int = 1):
    """全4チーム20問を1手ずつ進め、各手のあとのエンジンを返す。

    大きく外す回答を混ぜて、ゲームオーバー→エキシビションも通るようにする。
    """
    rng = random.Random(seed)
    engine = GameEngine()
    yield engine
    while engine.state is not State.FINISHED:
        if engine.state in (State.ANSWERING, State.EXHIBITION_ANSWERING) and engine.pending_answer is None:
            engine.submit_answer(rng.choice([0, 100, 100, 50, engine.current_question.correct]))
        else:
            engine.advance()
        yield engine


def restored_copy(engine: GameEngine) -> GameEngine:
    data = json.loads(json.dumps(engine.export_state()))  # ファイルを通したのと同じにする
    other = GameEngine()
    other.restore_state(data)
    return other


class TestEngineRoundTrip:
    def test_every_step_restores_the_same_screen(self):
        """どの瞬間に PC が落ちても、読み戻した画面が落ちる前と同じになる。"""
        codes = fixed_codes(GameEngine())
        states = set()
        for engine in play_steps():
            states.add(engine.state)
            other = restored_copy(engine)
            assert other.snapshot() == engine.snapshot()
            assert build_host_state(other, None, codes) == build_host_state(engine, None, codes)
        # エキシビションまで通ったことを確かめておく（通らないとテストの意味が薄い）
        assert State.EXHIBITION_ANSWERING in states
        assert State.FINISHED in states

    def test_game_continues_identically_after_restore(self):
        """途中から再開して最後まで進めても、結果が変わらない。"""
        steps = list(play_steps(seed=3))
        midway = None
        for engine in play_steps(seed=3):
            if engine.state is State.ANSWERING and engine.current_team.number == 2:
                midway = restored_copy(engine)
                break
        assert midway is not None
        final = steps[-1]
        # 途中までに出した回答の続きを、同じ順に入れて最後まで進める
        answers = [r.answer for t in final.teams for r in t.results]
        done = sum(t.finished_rounds for t in midway.teams)
        answers = iter(answers[done:])
        while midway.state is not State.FINISHED:
            if midway.state in (State.ANSWERING, State.EXHIBITION_ANSWERING) and midway.pending_answer is None:
                midway.submit_answer(next(answers))
            else:
                midway.advance()
        assert [t.balloons for t in midway.teams] == [t.balloons for t in final.teams]
        assert midway.winner() == final.winner()

    def test_pending_answer_survives(self):
        engine = GameEngine()
        for _ in range(3):
            engine.advance()
        engine.submit_answer(42)
        assert restored_copy(engine).pending_answer == 42


class TestRestoreRejects:
    def state_after_one_round(self) -> dict:
        engine = GameEngine()
        for _ in range(3):
            engine.advance()
        engine.submit_answer(10)
        engine.advance()
        return json.loads(json.dumps(engine.export_state()))

    def test_different_teams(self):
        data = self.state_after_one_round()
        engine = GameEngine(teams=[Team(number=i, name=f"別{i}") for i in range(1, 5)])
        with pytest.raises(ValueError, match="チーム構成"):
            engine.restore_state(data)

    def test_different_questions(self):
        data = self.state_after_one_round()
        data["teams"][0]["results"][0]["question_id"] = 999
        with pytest.raises(ValueError, match="問題"):
            GameEngine().restore_state(data)

    def test_balloons_do_not_match_results(self):
        data = self.state_after_one_round()
        data["teams"][0]["balloons"] = 3
        with pytest.raises(ValueError, match="バルーン"):
            GameEngine().restore_state(data)

    @pytest.mark.parametrize("key,value", [("state", 99), ("team_idx", 9), ("round_no", 6), ("pending_answer", 101)])
    def test_out_of_range(self, key, value):
        data = self.state_after_one_round()
        data[key] = value
        with pytest.raises(ValueError):
            GameEngine().restore_state(data)

    def test_failed_restore_leaves_engine_untouched(self):
        data = self.state_after_one_round()
        data["teams"][3]["results"] = [{"broken": True}]
        engine = GameEngine()
        before = engine.export_state()
        with pytest.raises(ValueError):
            engine.restore_state(data)
        assert engine.export_state() == before


class TestTeamCodes:
    def test_round_trip(self):
        codes = fixed_codes(GameEngine(), seed=5)
        other = TeamCodes([1, 2, 3, 4])
        other.restore(json.loads(json.dumps(codes.export())))
        assert [other.for_team(n) for n in range(1, 5)] == [codes.for_team(n) for n in range(1, 5)]

    @pytest.mark.parametrize("saved", [{"1": "1234"}, {"1": "12", "2": "1", "3": "1", "4": "1"}])
    def test_rejects(self, saved):
        with pytest.raises(ValueError):
            TeamCodes([1, 2, 3, 4]).restore(saved)


class TestSaveFile:
    def test_save_and_load(self, tmp_path):
        engine = GameEngine()
        engine.advance()
        codes = fixed_codes(engine)
        path = tmp_path / "run" / "game.json"
        savefile.save(path, engine, codes)
        data = savefile.load(path)
        other, other_codes = GameEngine(), TeamCodes([1, 2, 3, 4])
        savefile.restore(data, other, other_codes)
        assert other.state is State.TEAM_INTRO
        assert other_codes.for_team(1) == codes.for_team(1)
        assert not list(tmp_path.glob("run/*.tmp"))

    def test_load_rejects_broken_file(self, tmp_path):
        path = tmp_path / "game.json"
        path.write_text("{not json")
        with pytest.raises(ValueError):
            savefile.load(path)

    def test_back_up(self, tmp_path):
        path = tmp_path / "game.json"
        assert savefile.back_up(path) is None
        path.write_text("{}")
        prev = savefile.back_up(path)
        assert prev == tmp_path / "game.prev.json" and prev.exists() and not path.exists()

    def test_check_does_not_touch_engine(self):
        source = GameEngine()
        source.advance()
        data = json.loads(json.dumps({"game": source.export_state(), "player_codes": fixed_codes(source).export()}))
        engine = GameEngine()
        assert savefile.check(data, engine) is True
        assert engine.state is State.IDLE

    def test_idle_and_finished_are_not_resumable(self):
        steps = list(play_steps())
        assert not savefile.is_resumable(steps[0])
        assert not savefile.is_resumable(steps[-1])


class TestDescribeCommand:
    def run(self, capsys, *args) -> str:
        assert savefile.main(list(args)) == 0
        return capsys.readouterr().out.strip()

    def test_missing(self, tmp_path, capsys):
        assert self.run(capsys, "--describe", str(tmp_path / "none.json")) == "none"

    def test_resumable(self, tmp_path, capsys):
        engine = GameEngine()
        for _ in range(3):
            engine.advance()
        path = tmp_path / "game.json"
        savefile.save(path, engine, fixed_codes(engine))
        out = self.run(capsys, "--describe", str(path))
        assert out.startswith("チーム1 チーム1・1問目・回答受付")

    def test_finished_is_none(self, tmp_path, capsys):
        final = list(play_steps())[-1]
        path = tmp_path / "game.json"
        savefile.save(path, final, fixed_codes(final))
        assert self.run(capsys, "--describe", str(path)) == "none"

    def test_invalid(self, tmp_path, capsys):
        path = tmp_path / "game.json"
        path.write_text("{}")
        assert self.run(capsys, "--describe", str(path)).startswith("invalid:")


class TestController:
    def test_on_change_after_advance_and_answer(self):
        calls = []
        controller = GameController(GameEngine(), on_change=lambda: calls.append(controller.engine.state))
        for _ in range(3):
            controller.advance()
        controller.answer(30)
        assert calls == [State.TEAM_INTRO, State.QUESTION, State.ANSWERING, State.ANSWERING]


class TestSetupSave:
    def test_fresh_start_backs_up_and_saves_on_change(self, tmp_path):
        path = tmp_path / "game.json"
        path.write_text("old")
        dashboard = Dashboard(GameEngine(), None)
        setup_save(dashboard, path, False)
        assert (tmp_path / "game.prev.json").read_text() == "old"
        dashboard.controller.advance()
        assert savefile.load(path)["game"]["state"] == int(State.TEAM_INTRO)

    def test_resume(self, tmp_path):
        path = tmp_path / "game.json"
        first = Dashboard(GameEngine(), None)
        setup_save(first, path, False)
        for _ in range(3):
            first.controller.advance()
        first.controller.answer(77)

        second = Dashboard(GameEngine(), None)
        setup_save(second, path, True)
        assert second.engine.state is State.ANSWERING
        assert second.engine.pending_answer == 77
        assert second.player_codes.for_team(1) == first.player_codes.for_team(1)

    def test_resume_without_file_starts_fresh(self, tmp_path):
        dashboard = Dashboard(GameEngine(), None)
        setup_save(dashboard, tmp_path / "game.json", True)
        assert dashboard.engine.state is State.IDLE

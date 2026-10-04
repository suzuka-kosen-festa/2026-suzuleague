"""司会者画面（スマホから進行を操作する仕組み）のテスト。

Render への通信は偽物に差し替えるので、ネットワークは使わない
（serve_local のテストだけ 127.0.0.1 で待ち受ける）。
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from suzuleague.controller import GameController
from suzuleague.engine import GameEngine, GameError, State
from suzuleague.host import (
    HostRelay,
    build_host_state,
    execute_command,
    http_base_from_cloud_host,
    next_action_label,
    serve_local,
)
from suzuleague.models import Question
from suzuleague.questions import QuestionSet


def make_engine() -> GameEngine:
    qs = QuestionSet([Question(i + 1, f"Q{i + 1}", 50, "テスト") for i in range(20)])
    return GameEngine(question_set=qs)


def advance_to(engine: GameEngine, state: State) -> None:
    while engine.state is not state:
        if engine.state in (State.ANSWERING, State.EXHIBITION_ANSWERING):
            engine.submit_answer(40)
        engine.advance()


class FakeTransport:
    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.posted: list[dict] = []

    def queue(self, **command) -> None:
        command["seq"] = len(self.commands) + 1
        self.commands.append(command)

    def get_commands(self, after: int):
        latest = self.commands[-1]["seq"] if self.commands else 0
        return latest, [c for c in self.commands if c["seq"] > after]

    def post_state(self, state: dict) -> None:
        self.posted.append(state)


class TestHttpBase:
    @pytest.mark.parametrize(
        ("cloud_host", "expected"),
        [
            ("wss://suzuleague-cloud.onrender.com", "https://suzuleague-cloud.onrender.com"),
            ("ws://localhost:9080/", "http://localhost:9080"),
            ("https://example.com", "https://example.com"),
        ],
    )
    def test_converts_scheme(self, cloud_host, expected):
        assert http_base_from_cloud_host(cloud_host) == expected

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            http_base_from_cloud_host("example.com")


class TestNextActionLabel:
    def test_labels_follow_the_game(self):
        engine = make_engine()
        assert next_action_label(engine) == "1チーム目を登場させる"
        advance_to(engine, State.QUESTION)
        assert next_action_label(engine) == "回答受付を始める"
        advance_to(engine, State.ROUND_RESULT)
        assert next_action_label(engine) == "2問目を出題する"

    def test_last_round_and_last_team(self):
        engine = make_engine()
        advance_to(engine, State.TEAM_RESULT)
        assert next_action_label(engine) == "次のチームを登場させる"
        for _ in range(3):
            engine.advance()
            advance_to(engine, State.TEAM_RESULT)
        assert next_action_label(engine) == "全体結果を出す"
        engine.advance()
        assert next_action_label(engine) is None


class TestBuildHostState:
    def test_idle(self):
        state = build_host_state(make_engine())
        assert state["state"] == 0
        assert state["team"] is None
        assert state["question"] is None
        assert len(state["teams"]) == 4
        assert state["finished"] is False

    def test_question_includes_correct_answer_for_the_host(self):
        engine = make_engine()
        advance_to(engine, State.QUESTION)
        state = build_host_state(engine)
        assert state["question"] == {"id": 1, "text": "Q1", "source": "テスト", "correct": 50}

    def test_waiting_for_answer(self):
        engine = make_engine()
        advance_to(engine, State.ANSWERING)
        state = build_host_state(engine)
        assert state["accepting_answer"] is True
        assert state["needs_answer"] is True
        engine.submit_answer(45)
        state = build_host_state(engine)
        assert state["needs_answer"] is False
        assert state["answer"] == 45

    def test_reveal_shows_result(self):
        engine = make_engine()
        advance_to(engine, State.ANSWERING)
        engine.submit_answer(30)
        engine.advance()
        result = build_host_state(engine)["result"]
        assert result["answer"] == 30
        assert result["damage"] == 20
        assert result["balloons_after"] == 80

    def test_finished_has_winner(self):
        engine = make_engine()
        advance_to(engine, State.FINISHED)
        state = build_host_state(engine)
        assert state["finished"] is True
        assert state["next_action"] is None
        assert state["winner"]["number"] == 1  # 全チーム同点なら先頭
        assert all(t["status"] == "クリア" for t in state["teams"])

    def test_is_json_serializable(self):
        engine = make_engine()
        advance_to(engine, State.REVEAL)
        json.dumps(build_host_state(engine, {"seq": 1, "ok": True, "message": "x"}))


class TestExecuteCommand:
    def test_next(self):
        controller = GameController(make_engine())
        result = execute_command(controller, {"seq": 1, "type": "next", "expect_state": 0})
        assert result == {"seq": 1, "ok": True, "message": "「チーム紹介」に進みました"}

    def test_stale_next_is_ignored(self):
        """通信の遅れで「次へ」が二重に届いても、2段階進まない。"""
        controller = GameController(make_engine())
        execute_command(controller, {"seq": 1, "type": "next", "expect_state": 0})
        result = execute_command(controller, {"seq": 2, "type": "next", "expect_state": 0})
        assert result["ok"] is False
        assert controller.engine.state is State.TEAM_INTRO

    def test_next_without_answer_fails_gracefully(self):
        engine = make_engine()
        advance_to(engine, State.ANSWERING)
        result = execute_command(GameController(engine), {"seq": 1, "type": "next"})
        assert result["ok"] is False
        assert "回答" in result["message"]

    def test_answer(self):
        engine = make_engine()
        advance_to(engine, State.ANSWERING)
        result = execute_command(GameController(engine), {"type": "answer", "value": 77})
        assert result["ok"] is True
        assert engine.pending_answer == 77

    @pytest.mark.parametrize("value", [None, "50", 50.5, True, 101])
    def test_bad_answer(self, value):
        engine = make_engine()
        advance_to(engine, State.ANSWERING)
        result = execute_command(GameController(engine), {"type": "answer", "value": value})
        assert result["ok"] is False
        assert engine.pending_answer is None

    def test_unknown_command(self):
        result = execute_command(GameController(make_engine()), {"type": "reset"})
        assert result["ok"] is False


class TestController:
    def test_push_error_does_not_stop_the_game(self):
        class BrokenBridge:
            def push(self, snapshot):
                raise OSError("network down")

        errors = []
        controller = GameController(make_engine(), BrokenBridge(), on_push_error=errors.append)
        controller.advance()
        assert controller.engine.state is State.TEAM_INTRO
        assert len(errors) == 1

    def test_expect_state_mismatch(self):
        controller = GameController(make_engine())
        with pytest.raises(GameError):
            controller.advance(expect_state=3)


class TestHostRelay:
    def test_commands_queued_before_start_are_skipped(self):
        """裏方PCの再起動後に、古い「次へ」が一気に流れ込まない。"""
        transport = FakeTransport()
        transport.queue(type="next")
        transport.queue(type="next")
        relay = HostRelay(GameController(make_engine()), transport)
        relay.step()
        assert relay.controller.engine.state is State.IDLE
        assert transport.posted[-1]["state"] == 0

    def test_executes_new_commands_and_reports(self):
        transport = FakeTransport()
        seen = []
        relay = HostRelay(
            GameController(make_engine()), transport, on_command=lambda c, r: seen.append(r)
        )
        relay.step()
        transport.queue(type="next", expect_state=0)
        relay.step()
        assert relay.controller.engine.state is State.TEAM_INTRO
        assert transport.posted[-1]["state"] == 1
        assert transport.posted[-1]["last_command"]["seq"] == 1
        assert seen[0]["ok"] is True
        relay.step()  # 同じ操作を二度実行しない
        assert relay.controller.engine.state is State.TEAM_INTRO

    def test_reposts_only_on_change_or_interval(self):
        transport = FakeTransport()
        now = [0.0]
        relay = HostRelay(
            GameController(make_engine()), transport, state_interval=2.0, clock=lambda: now[0]
        )
        relay.step()
        relay.step()
        assert len(transport.posted) == 1
        now[0] = 2.5  # 変化がなくても定期的に送る（スマホ側の「PCと接続中」表示のため）
        relay.step()
        assert len(transport.posted) == 2
        relay.controller.advance()  # CLIなど別の入口から進んでも送る
        relay.step()
        assert len(transport.posted) == 3


class TestServeLocal:
    def test_round_trip(self):
        controller = GameController(make_engine())
        server = serve_local(controller, port=0)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/host") as res:
                assert "司会者画面" in res.read().decode()
            req = urllib.request.Request(
                base + "/api/host/command",
                data=json.dumps({"type": "next", "expect_state": 0}).encode(),
                method="POST",
            )
            with urllib.request.urlopen(req) as res:
                body = json.loads(res.read())
            assert body["result"]["ok"] is True
            with urllib.request.urlopen(base + "/api/host/state") as res:
                state = json.loads(res.read())["state"]
            assert state["state"] == 1
            assert state["last_command"]["seq"] == body["seq"]
        finally:
            server.shutdown()

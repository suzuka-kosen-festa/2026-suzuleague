"""部門対抗モードの画面向けの状態・操作・合言葉・保存のテスト。"""

import json
import urllib.error
import urllib.request

import pytest

from suzuleague import savefile
from suzuleague.battle import BattleEngine
from suzuleague.battle_app import BattleApp
from suzuleague.battle_host import (
    build_host_state,
    build_player_state,
    execute_command,
    load_or_create_codes,
)
from suzuleague.engine import State
from suzuleague.host import serve_local
from suzuleague.models import Question, Team


def make_app(corrects=(40, 70), n_teams=3, hold=0.0) -> BattleApp:
    teams = [Team(i, f"部門{i}") for i in range(1, n_teams + 1)]
    questions = [Question(200 + i, f"Q{i}", c) for i, c in enumerate(corrects, start=1)]
    engine = BattleEngine(teams, questions)
    codes = load_or_create_codes(engine, None)
    app = BattleApp(engine, codes, reveal_hold=hold)
    app.controller._min_advance_interval = 0.0
    return app


def host(app: BattleApp, **command):
    return execute_command(app.controller, app.codes, command)


def player(app: BattleApp, team_no: int, value: int, **extra):
    q = app.engine.current_question
    command = {
        "type": "answer",
        "source": "player",
        "code": app.codes.for_team(team_no),
        "team": team_no,
        "value": value,
        "question_id": q.id if q else None,
    }
    command.update(extra)
    return execute_command(app.controller, app.codes, command)


def to_answering(app: BattleApp) -> None:
    assert host(app, type="next")["ok"]
    assert host(app, type="next")["ok"]
    assert app.engine.state is State.ANSWERING


class TestPublicState:
    def test_answers_hidden_until_open(self):
        app = make_app()
        to_answering(app)
        assert player(app, 1, 33)["ok"]
        state = build_player_state(app.engine)
        team1 = state["teams"][0]
        assert team1["answered"] is True
        assert team1["answer"] is None
        assert "33" not in json.dumps(state)
        assert state["accepting_answer"] is True

    def test_correct_hidden_until_reveal(self):
        app = make_app(corrects=(77,))
        to_answering(app)
        for n in (1, 2, 3):
            player(app, n, 10)
        host(app, type="next")  # 予想オープン
        state = build_player_state(app.engine)
        assert state["state"] == int(State.OPEN)
        assert state["correct"] is None
        assert state["teams"][0]["answer"] == 10
        assert "77" not in json.dumps(state)
        host(app, type="next")  # 正解発表
        state = build_player_state(app.engine)
        assert state["correct"] == 77
        assert state["teams"][0]["damage"] == 67

    def test_reveal_hold_does_not_hide_correct(self):
        app = make_app(corrects=(77,), hold=4.0)
        to_answering(app)
        for n in (1, 2, 3):
            player(app, n, 10)
        host(app, type="next")
        host(app, type="next")
        assert app.controller.hold_remaining() > 0
        state = app.build_state(None, app.controller.hold_remaining())
        assert state["player"]["correct"] == 77  # 大画面自身が正解を出すので隠さない
        assert state["hold"] >= 1
        result = host(app, type="next")
        assert not result["ok"]  # 数え下げの演出中は次へ進ませない

    def test_codes_only_in_host_state(self):
        app = make_app()
        state = build_host_state(app.engine, app.codes)
        code = app.codes.for_team(1)
        assert state["player_codes"]["1"] == code
        assert code not in json.dumps(state["player"])

    def test_inactive_team_not_public(self):
        app = make_app()
        assert host(app, type="set_active", team=2, active=False)["ok"]
        state = build_player_state(app.engine)
        assert [t["number"] for t in state["teams"]] == [1, 3]
        host_state = build_host_state(app.engine, app.codes)
        assert [t["active"] for t in host_state["teams"]] == [True, False, True]


class TestCommands:
    def test_wrong_code_rejected(self):
        app = make_app()
        to_answering(app)
        result = player(app, 1, 50, code="0000" if app.codes.for_team(1) != "0000" else "0001")
        assert not result["ok"]
        assert app.engine.answers == {}

    def test_code_and_team_must_match(self):
        app = make_app()
        to_answering(app)
        result = player(app, 1, 50, team=2)
        assert not result["ok"]

    def test_old_question_rejected(self):
        app = make_app()
        to_answering(app)
        result = player(app, 1, 50, question_id=999)
        assert not result["ok"]

    def test_player_cannot_advance(self):
        app = make_app()
        result = execute_command(app.controller, app.codes, {"type": "next", "source": "player"})
        assert not result["ok"]
        assert app.engine.state is State.IDLE

    def test_host_proxy_answer(self):
        app = make_app()
        to_answering(app)
        result = host(app, type="answer", team=3, value=12)
        assert result["ok"]
        assert "部門3" in result["message"]
        assert app.engine.answers == {3: 12}

    def test_next_blocked_and_close(self):
        app = make_app()
        to_answering(app)
        player(app, 1, 40)
        result = host(app, type="next", expect_state=int(State.ANSWERING))
        assert not result["ok"]
        assert "部門2" in result["message"]
        assert host(app, type="close", expect_state=int(State.ANSWERING))["ok"]
        assert app.engine.state is State.OPEN

    def test_stale_close_ignored(self):
        app = make_app()
        to_answering(app)
        result = host(app, type="close", expect_state=int(State.QUESTION))
        assert not result["ok"]
        assert app.engine.state is State.ANSWERING

    def test_changes_are_saved(self):
        app = make_app()
        calls = []
        app.controller.on_change = lambda: calls.append(1)
        host(app, type="set_active", team=1, active=False)
        assert calls


class TestCodes:
    def test_codes_persist(self, tmp_path):
        path = tmp_path / "codes.json"
        engine = make_app().engine
        first = load_or_create_codes(engine, path)
        second = load_or_create_codes(make_app().engine, path)
        assert first.export() == second.export()

    def test_codes_reject_other_teams(self, tmp_path):
        path = tmp_path / "codes.json"
        load_or_create_codes(make_app(n_teams=3).engine, path)
        with pytest.raises(ValueError, match="消して"):
            load_or_create_codes(make_app(n_teams=4).engine, path)


class TestSaveFile:
    def test_round_trip(self, tmp_path):
        app = make_app()
        to_answering(app)
        player(app, 2, 61)
        path = tmp_path / "game.json"
        savefile.save(path, app.engine, app.codes)

        other = make_app()
        data = savefile.load(path)
        assert savefile.check(data, other.engine)
        savefile.restore(data, other.engine, other.codes)
        assert other.engine.state is State.ANSWERING
        assert other.engine.answers == {2: 61}
        assert other.codes.export() == app.codes.export()
        assert "部門対抗・1問目" in savefile.describe(other.engine, data["saved_at"])


@pytest.fixture
def local_server():
    app = make_app(corrects=(40,))
    server = serve_local(
        app.controller, port=0, player_codes=app.codes, build_state=app.build_state, execute=app.execute
    )
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield app, base
    server.shutdown()


def call(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class TestLocalServer:
    def test_pages(self, local_server):
        _, base = local_server
        for page in ("/host", "/player.html", "/board.html", "/furikaeri.html", "/furikaeri-host.html"):
            with urllib.request.urlopen(base + page, timeout=5) as res:
                assert res.status == 200

    def test_player_flow(self, local_server):
        app, base = local_server
        status, body = call(base, "/api/player/check", {"code": app.codes.for_team(2)})
        assert status == 200 and body == {"team": 2, "name": "部門2"}
        status, _ = call(base, "/api/player/check", {"code": "abcd"})
        assert status == 403

        call(base, "/api/host/command", {"type": "next"})
        app.controller._last_advance = None
        status, _ = call(base, "/api/player/answer", {"code": app.codes.for_team(2), "value": 5, "question_id": 201})
        assert status == 409  # 出題中（読み上げ）はまだ受け付けない
        call(base, "/api/host/command", {"type": "next"})
        status, _ = call(base, "/api/player/answer", {"code": app.codes.for_team(2), "value": 5, "question_id": 201})
        assert status == 200
        assert app.engine.answers == {2: 5}

        status, body = call(base, "/api/player/state")
        assert body["state"]["teams"][1]["answered"] is True
        assert body["state"]["teams"][1]["answer"] is None


class TestReviewFixes:
    """2026-11-12 の検品で見つかった点。"""

    def test_codes_are_six_digits(self):
        app = make_app()
        assert all(len(c) == 6 for c in app.codes.export().values())

    def test_codes_kept_when_finished(self):
        app = make_app(corrects=(50,))
        to_answering(app)
        host(app, type="close")
        for _ in range(3):
            app.controller._last_advance = None
            host(app, type="next")
        assert app.engine.state is State.FINISHED
        state = build_host_state(app.engine, app.codes)
        assert state["player_codes"]["1"] == app.codes.for_team(1)
        assert state["player"]["accepting_answer"] is False

    def test_resume_keeps_codes_from_file(self, tmp_path):
        from suzuleague.battle_app import setup_save

        codes_file = tmp_path / "codes.json"
        save_file = tmp_path / "game.json"
        app = make_app()
        app.codes = load_or_create_codes(app.engine, codes_file)
        setup_save(app, save_file, resume=False)
        to_answering(app)  # 保存される
        old = app.codes.export()

        codes_file.unlink()  # 合言葉を作り直してカードを刷り直した
        again = make_app()
        again.codes = load_or_create_codes(again.engine, codes_file)
        setup_save(again, save_file, resume=True)
        assert again.engine.state is State.ANSWERING
        assert again.codes.export() != old
        assert again.codes.export() == load_or_create_codes(again.engine, codes_file).export()

    def test_idle_with_inactive_team_is_resumable(self):
        app = make_app()
        assert not savefile.is_resumable(app.engine)
        host(app, type="set_active", team=2, active=False)
        assert savefile.is_resumable(app.engine)

    def test_restore_rejects_inconsistent_state(self):
        app = make_app()
        data = app.engine.export_state()
        data["state"] = int(State.ANSWERING)  # 問題の番号は0のまま
        with pytest.raises(ValueError, match="合いません"):
            make_app().engine.restore_state(data)

    def test_local_server_rejects_non_integer_answer(self, local_server):
        app, base = local_server
        call(base, "/api/host/command", {"type": "next"})
        app.controller._last_advance = None
        call(base, "/api/host/command", {"type": "next"})
        status, _ = call(base, "/api/player/answer", {"code": app.codes.for_team(1), "value": "50", "question_id": 201})
        assert status == 400

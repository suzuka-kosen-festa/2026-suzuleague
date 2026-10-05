"""司会者画面（司会のスマホで進行を操作する画面）のサーバ側。

司会のスマホから裏方PCへは直接つながない。会場Wi-Fiでスマホから PC に
届く保証がないため、Render の cloud-server を郵便受けにして中継する。

    司会のスマホ ──操作を預ける──▶ Render ◀──取りに行く── 裏方PC（このモジュール）
                ◀──状態を見る────         ◀──状態を預ける──

- HostRelay: Render から操作を取り出して実行し、状態を預け直すスレッド
- serve_local: Render に届かないときの予備。同じ画面を裏方PC上で直接開く

出演者の回答画面（player.html）も同じ中継に相乗りする。出演者は自分のスマホから
回答を Render に預け、裏方PCが司会の操作と一緒に取り出す。観客のなりすましを
防ぐため、チームごとの4桁の合言葉（TeamCodes）を司会者画面に出し、司会が出演者に伝える。

画面の本体は host.html。Render（cloud-server の public/）と serve_local の
どちらから配信しても動くよう、APIは相対パスで呼んでいる。
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .controller import GameController
from .engine import GameEngine, GameError, State
from .labels import STATE_LABELS, team_status_label
from .models import RoundResult, Team
from .questions import ROUNDS_PER_TEAM

ENV_HOST_TOKEN = "SUZULEAGUE_HOST_TOKEN"
HOST_PAGE_PATH = Path(__file__).parent / "host.html"
PLAYER_PAGE_PATH = Path(__file__).parent / "player.html"
TOKEN_HEADER = "X-Host-Token"

ANSWERING_STATES = (State.ANSWERING, State.EXHIBITION_ANSWERING)
REVEAL_STATES = (State.REVEAL, State.ROUND_RESULT)


def http_base_from_cloud_host(cloud_host: str) -> str:
    """cloudサーバのURL（ws/wss）から、同じサーバのHTTP(S)のURLを作る。"""
    host = cloud_host.strip().rstrip("/")
    if host.startswith("wss://"):
        return "https://" + host.removeprefix("wss://")
    if host.startswith("ws://"):
        return "http://" + host.removeprefix("ws://")
    if host.startswith(("https://", "http://")):
        return host
    raise ValueError(f"cloudサーバのURLではありません: {cloud_host!r}")


# ---- 出演者の合言葉 ------------------------------------------------


class TeamCodes:
    """出演者の回答画面に入力してもらう、チームごとの4桁の合言葉。

    裏方PCを起動するたびに作り直す。司会者画面とCLIに出るので、
    司会がチームの登場時に出演者へ伝える。
    """

    def __init__(self, team_numbers: list[int], randbelow: Callable[[int], int] = secrets.randbelow) -> None:
        self._codes: dict[int, str] = {}
        used: set[str] = set()
        for number in team_numbers:
            code = f"{randbelow(10_000):04d}"
            while code in used:  # 前のチームの合言葉で次のチームの回答ができないように
                code = f"{randbelow(10_000):04d}"
            used.add(code)
            self._codes[number] = code

    def for_team(self, team_number: int) -> str | None:
        return self._codes.get(team_number)


# ---- 画面に出す状態 ------------------------------------------------


def next_action_label(engine: GameEngine) -> str | None:
    """「次へ」を押すと何が起きるか。ボタンに書いて押し間違いを防ぐ。"""
    state = engine.state
    if state is State.IDLE:
        return "1チーム目を登場させる"
    if state is State.TEAM_INTRO:
        return "1問目を出題する"
    if state is State.QUESTION:
        return "回答受付を始める"
    if state in ANSWERING_STATES:
        return "正解を発表する"
    if state is State.REVEAL:
        return "ラウンド結果を出す"
    if state is State.ROUND_RESULT:
        round_no = engine.snapshot().round_no
        if round_no >= ROUNDS_PER_TEAM:
            return "チーム結果を出す"
        return f"{round_no + 1}問目を出題する"
    if state is State.TEAM_RESULT:
        team = engine.current_team
        if team is not None and team is engine.teams[-1]:
            return "全体結果を出す"
        return "次のチームを登場させる"
    return None  # FINISHED


def _team_summary(engine: GameEngine) -> list[dict[str, Any]]:
    return [
        {
            "number": t.number,
            "name": t.name,
            "balloons": t.balloons,
            "finished_rounds": t.finished_rounds,
            "status": team_status_label(t),
        }
        for t in engine.teams
    ]


def _team_info(team: Team | None) -> dict[str, Any] | None:
    if team is None:
        return None
    return {"number": team.number, "name": team.name, "balloons": team.balloons, "failed": team.is_failed}


def _shown_result(engine: GameEngine) -> dict[str, Any] | None:
    """正解発表とラウンド結果の間だけ出す、直前の問題の結果。"""
    result: RoundResult | None = engine.last_result if engine.state in REVEAL_STATES else None
    if result is None:
        return None
    return {
        "answer": result.answer,
        "correct": result.correct,
        "damage": result.damage,
        "balloons_after": result.balloons_after,
        "exhibition": result.exhibition,
        "perfect": result.is_perfect,
    }


def _winner_info(engine: GameEngine) -> dict[str, Any] | None:
    winner = engine.winner() if engine.state is State.FINISHED else None
    if winner is None:
        return None
    return {"number": winner.number, "name": winner.name, "balloons": winner.balloons}


def _shows_question(engine: GameEngine) -> bool:
    """チーム紹介の間は、次の問題をまだ出さない。"""
    return engine.current_question is not None and engine.state is not State.TEAM_INTRO


def build_player_state(engine: GameEngine) -> dict[str, Any]:
    """出演者の回答画面に出す状態。誰でも見られるので、**正解は発表後だけ**入れる。"""
    question = engine.current_question
    return {
        "state": int(engine.state),
        "state_label": STATE_LABELS[engine.state],
        "team": _team_info(engine.current_team),
        "round": engine.snapshot().round_no,
        "rounds_per_team": ROUNDS_PER_TEAM,
        "question": (
            {"id": question.id, "text": question.text}
            if question and _shows_question(engine)
            else None
        ),
        "accepting_answer": engine.state in ANSWERING_STATES,
        "exhibition": engine.state is State.EXHIBITION_ANSWERING,
        "answer": engine.pending_answer,
        "result": _shown_result(engine),
        "teams": _team_summary(engine),
        # 発表済みの問題数。観客ランキングで未回答の問題を数えるのに使う（cloud-server）
        "revealed": sum(t.finished_rounds for t in engine.teams),
        "finished": engine.state is State.FINISHED,
        "winner": _winner_info(engine),
    }


def build_host_state(
    engine: GameEngine,
    last_command: dict[str, Any] | None = None,
    player_codes: TeamCodes | None = None,
) -> dict[str, Any]:
    """司会者画面に出す状態。正解も含む（画面側で押したときだけ表示する）。

    `player` は出演者の回答画面向けの公開用の状態で、Render がそのまま配る。
    `player_code` は Render が出演者の回答を受け付けるときの照合に使う。
    """
    team = engine.current_team
    question = engine.current_question
    return {
        "state": int(engine.state),
        "state_label": STATE_LABELS[engine.state],
        "next_action": next_action_label(engine),
        "needs_answer": engine.state in ANSWERING_STATES
        and engine.pending_answer is None,
        "accepting_answer": engine.state in ANSWERING_STATES,
        "exhibition": engine.state is State.EXHIBITION_ANSWERING,
        "team": _team_info(team),
        "round": engine.snapshot().round_no,
        "rounds_per_team": ROUNDS_PER_TEAM,
        "question": (
            {
                "id": question.id,
                "text": question.text,
                "source": question.source,
                "correct": question.correct,
            }
            if question and _shows_question(engine)
            else None
        ),
        "answer": engine.pending_answer,
        "result": _shown_result(engine),
        "teams": _team_summary(engine),
        "finished": engine.state is State.FINISHED,
        "winner": _winner_info(engine),
        "last_command": last_command,
        "player_code": (
            player_codes.for_team(team.number)
            if player_codes and team and engine.state is not State.FINISHED
            else None
        ),
        "player": build_player_state(engine),
    }


# ---- 操作の実行 ----------------------------------------------------


def _check_player_answer(
    controller: GameController, command: dict[str, Any], player_codes: TeamCodes | None
) -> None:
    """出演者の回答が、いま登壇中のチームの、いまの問題へのものか確かめる。

    Render でも合言葉は照合しているが、問題が切り替わった直後に届いた
    1つ前の問題への回答などはここで弾く。
    """
    engine = controller.engine
    team = engine.current_team
    question = engine.current_question
    if player_codes is None or team is None:
        raise GameError("出演者の回答を受け付けていません")
    if command.get("code") != player_codes.for_team(team.number):
        raise GameError("出演者の合言葉が違うため、回答を受け付けませんでした")
    if question is None or command.get("question_id") != question.id:
        raise GameError("前の問題への回答が遅れて届いたため、受け付けませんでした")


def execute_command(
    controller: GameController,
    command: dict[str, Any],
    player_codes: TeamCodes | None = None,
) -> dict[str, Any]:
    """司会者画面・出演者の回答画面から届いた操作を1つ実行し、結果を返す。

    失敗しても例外は投げない。結果は画面に表示して司会に知らせる。
    """
    seq = command.get("seq")
    kind = command.get("type")
    from_player = command.get("source") == "player"
    try:
        if from_player and kind != "answer":
            raise GameError("出演者からは回答しか受け付けません")
        if kind == "next":
            expect = command.get("expect_state")
            if expect is not None and not isinstance(expect, int):
                raise GameError("操作の形式が正しくありません")
            state = controller.advance(expect_state=expect)
            message = f"「{STATE_LABELS[state]}」に進みました"
        elif kind == "answer":
            value = command.get("value")
            if not isinstance(value, int) or isinstance(value, bool):
                raise GameError("回答は0〜100の整数で入力してください")
            with controller.lock:
                if from_player:
                    _check_player_answer(controller, command, player_codes)
                controller.answer(value)
            message = (
                f"出演者から回答 {value}% が届きました"
                if from_player
                else f"回答 {value}% を入力しました"
            )
        elif kind == "resync":
            controller.resync()
            message = "Scratch に今の状態を送り直しました"
        else:
            raise GameError(f"不明な操作です: {kind!r}")
    except GameError as e:
        return {"seq": seq, "ok": False, "message": str(e)}
    return {"seq": seq, "ok": True, "message": message}


# ---- Render 経由の中継 ---------------------------------------------


class HttpTransport:
    """Render（cloud-server）の司会者画面用APIとのやり取り。"""

    def __init__(self, base_url: str, token: str, timeout: float = 5.0) -> None:
        if not token:
            raise ValueError(f"合言葉が空です（環境変数 {ENV_HOST_TOKEN}）")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={TOKEN_HEADER: self.token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as res:
            return json.loads(res.read() or b"null")

    def get_commands(self, after: int) -> tuple[int, list[dict[str, Any]]]:
        data = self._request("GET", f"/api/host/command?after={after}")
        return int(data["latest"]), list(data["commands"])

    def post_state(self, state: dict[str, Any]) -> None:
        self._request("POST", "/api/host/state", state)


class HostRelay:
    """Render から操作を取り出して実行し、状態を預け直す。

    起動前に溜まっていた操作は実行しない（裏方PCの再起動後に、
    古い「次へ」が一気に流れ込むのを防ぐ）。
    """

    def __init__(
        self,
        controller: GameController,
        transport: HttpTransport,
        *,
        interval: float = 0.4,
        state_interval: float = 2.0,
        on_command: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
        on_status: Callable[[bool, str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        player_codes: TeamCodes | None = None,
    ) -> None:
        self.controller = controller
        self.player_codes = player_codes
        self.transport = transport
        self.interval = interval
        self.state_interval = state_interval
        self._on_command = on_command
        self._on_status = on_status
        self._clock = clock
        self._last_seq: int | None = None
        self._last_command: dict[str, Any] | None = None
        self._last_posted: str | None = None
        self._last_posted_at = float("-inf")
        self._healthy: bool | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def step(self) -> None:
        """1回分の処理（操作の取り出し・実行・状態の送信）。"""
        latest, commands = self.transport.get_commands(self._last_seq or 0)
        if self._last_seq is None:
            self._last_seq = latest  # 起動前の操作は捨てる
            commands = []
        for command in commands:
            seq = int(command.get("seq", 0))
            if seq <= self._last_seq:
                continue
            self._last_seq = seq
            result = execute_command(self.controller, command, self.player_codes)
            self._last_command = result
            if self._on_command:
                self._on_command(command, result)

        with self.controller.lock:
            state = build_host_state(
                self.controller.engine, self._last_command, self.player_codes
            )
        fingerprint = json.dumps(state, sort_keys=True, ensure_ascii=False)
        now = self._clock()
        if (
            fingerprint != self._last_posted
            or now - self._last_posted_at >= self.state_interval
        ):
            self.transport.post_state(state)
            self._last_posted = fingerprint
            self._last_posted_at = now

    def _set_healthy(self, healthy: bool, detail: str) -> None:
        if healthy != self._healthy:  # 状態が変わったときだけ知らせる
            self._healthy = healthy
            if self._on_status:
                self._on_status(healthy, detail)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
                self._set_healthy(True, "")
                wait = self.interval
            except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
                self._set_healthy(False, str(e))
                wait = 3.0  # Render が寝ている・落ちているときは間隔を空ける
            except Exception as e:  # 想定外でもスレッドは止めない（司会が操作できなくなる）
                self._set_healthy(False, f"{type(e).__name__}: {e}")
                wait = 3.0
            self._stop.wait(wait)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="host-relay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# ---- 予備：裏方PC上で直接開く -------------------------------------


def serve_local(
    controller: GameController,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    on_command: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    player_codes: TeamCodes | None = None,
) -> ThreadingHTTPServer:
    """司会者画面を裏方PC上で配信する（Render に届かないときの予備）。

    操作はその場で実行する。合言葉は要らない（127.0.0.1 でしか待ち受けないため）。
    """
    session = {"seq": 0, "last_command": None}
    session_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # CLIの表示を汚さない

        def _send(self, status: int, content_type: str, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, status: int, body: Any) -> None:
            data = json.dumps(body, ensure_ascii=False).encode()
            self._send(status, "application/json; charset=utf-8", data)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/host", "/host.html"):
                self._send(200, "text/html; charset=utf-8", HOST_PAGE_PATH.read_bytes())
            elif path == "/api/host/state":
                with controller.lock:
                    state = build_host_state(
                        controller.engine, session["last_command"], player_codes
                    )
                self._send_json(200, {"state": state, "age": 0})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/api/host/command":
                self._send_json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(length, 10_000)) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._send_json(400, {"error": "操作の形式が正しくありません"})
                return
            with session_lock:
                session["seq"] += 1
                # 予備の画面は司会専用。出演者の回答として扱わない
                command = {**body, "seq": session["seq"], "source": "host"}
                result = execute_command(controller, command)
                session["last_command"] = result
            if on_command:
                on_command(command, result)
            self._send_json(200, {"seq": command["seq"], "result": result})

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="host-local", daemon=True).start()
    return server

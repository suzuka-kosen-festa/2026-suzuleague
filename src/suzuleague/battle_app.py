"""部門対抗モードの起動（`uv run suzuleague --battle`）。

高専祭本番のダッシュボード（dashboard.py）と同じ引数で起動し、`--battle` のときだけここに来る。
クラウド変数（Scratch のステージ画面用）にはつながず、Render の郵便受けを通す司会者画面の中継だけを動かす。
大画面（board.html）と回答ページ（player.html）は、どちらも Render の公開用の状態を読む。

ターミナルの表示は1行の状態だけにしている。本番はアプリから --headless で動かし、
操作はすべて司会者画面で行うため（docs/furikaeri/design.md）。
"""

from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path
from typing import Any

from . import savefile
from .battle import BattleEngine
from .battle_host import (
    BATTLE_REVEAL_HOLD,
    build_host_state,
    execute_command,
    next_action_label,
)
from .cloud import resolve_cloud_host
from .controller import GameController
from .engine import GameError, State
from .host import ENV_HOST_TOKEN, HostRelay, HttpTransport, TeamCodes, http_base_from_cloud_host, serve_local
from .labels import STATE_LABELS
from .questions import FURIKAERI_QUESTIONS, FURIKAERI_REHEARSAL_QUESTIONS
from .teams import resolve_teams


HELP_TEXT = """\
コマンド:
  next  (n)             進行を1段階進める
  answer <部門> <0-100>  部門の回答を代理で入れる（例: answer 3 45）
  close                 未回答の部門を残したまま締め切る（予想50とみなす）
  status (s)            今の状態と順位
  quit                  終了
"""


def make_engine(teams_path: str | None, rehearsal: bool) -> BattleEngine:
    teams = resolve_teams(teams_path)
    questions = FURIKAERI_REHEARSAL_QUESTIONS if rehearsal else FURIKAERI_QUESTIONS
    return BattleEngine(teams, questions, rehearsal=rehearsal)


class BattleApp:
    def __init__(self, engine: BattleEngine, reveal_hold: float = BATTLE_REVEAL_HOLD) -> None:
        self.engine = engine
        # 部門対抗モードに合言葉はない。保存ファイルの形を高専祭本番と共通にするため、空のまま持つ
        self.codes = TeamCodes([])
        self.controller = GameController(engine, None, reveal_hold=reveal_hold, min_advance_interval=1.0)

    def build_state(self, last_command: dict[str, Any] | None, hold: float) -> dict[str, Any]:
        return build_host_state(self.engine, last_command, hold)

    def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        return execute_command(self.controller, command)

    # ---- 表示 ----------------------------------------------------

    def status_line(self) -> str:
        e = self.engine
        q = f"第{e.round_no}/{e.rounds}問" if e.round_no else "開始前"
        nxt = next_action_label(e)
        line = f"[{STATE_LABELS[e.state]}] {q}" + (f" → 次へで「{nxt}」" if nxt else "")
        if e.state is State.ANSWERING:
            missing = [t.name for t in e.missing_teams()]
            line += f"　まだ: {'、'.join(missing) if missing else 'なし'}"
        return line

    def print_status(self) -> None:
        print(self.status_line())
        if self.engine.round_no:
            print("  順位: " + " / ".join(
                f"{s.rank}位 {s.team.name} {s.team.balloons}" for s in self.engine.standings()
            ))

    def print_teams(self) -> None:
        for team in self.engine.teams:
            mark = "" if team.active else "（不参加）"
            print(f"  {team.number:>2} {team.name}{mark}")

    def on_command(self, command: dict[str, Any], result: dict[str, Any]) -> None:
        where = "回答ページ" if command.get("source") == "player" else "司会者画面"
        print(f"{'●' if result['ok'] else '×'} {where}: {result['message']}")
        if result["ok"] and command.get("type") in ("next", "close"):
            print("  " + self.status_line())

    def on_relay_status(self, healthy: bool, detail: str) -> None:
        if healthy:
            print("● 司会者画面（スマホ）と中継がつながりました")
        else:
            print(f"● 司会者画面（スマホ）との中継が切れました: {detail}（自動で再接続します）")

    # ---- 操作 ----------------------------------------------------

    def run_line(self, line: str) -> bool:
        """CLI の1行を実行する。終了なら False。"""
        cmd, _, arg = line.strip().partition(" ")
        cmd = cmd.lower()
        try:
            if cmd in ("quit", "exit"):
                return False
            if cmd in ("next", "n"):
                self.controller.advance()
                self.print_status()
            elif cmd in ("answer", "a"):
                parts = arg.split()
                if len(parts) != 2 or not all(p.isdigit() for p in parts):
                    print("使い方: answer <部門の番号> <0-100>")
                    return True
                result = self.execute({"type": "answer", "team": int(parts[0]), "value": int(parts[1])})
                print(result["message"])
            elif cmd == "close":
                print(self.execute({"type": "close"})["message"])
            elif cmd in ("status", "s"):
                self.print_status()
            elif cmd in ("help", "h"):
                print(HELP_TEXT)
            elif cmd:
                print(f"不明なコマンド: {cmd}（help 参照）")
        except GameError as e:
            print(e)
        return True

    def run(self) -> None:
        print("スズリーグ 部門対抗モード（help でコマンド一覧）")
        self.print_status()
        while True:
            try:
                line = input("furikaeri> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not self.run_line(line):
                break
        print("終了しました")

    def run_headless(self) -> None:
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        print("スズリーグ 部門対抗モード（ターミナルなし。操作は司会者画面から）")
        self.print_status()
        while not stop.wait(1.0):
            pass
        print("終了しました")


def setup_save(app: BattleApp, path: Path, resume: bool) -> None:
    """続きから始めるなら保存を読み戻し、以降は操作のたびに保存する（高専祭本番と同じ）。"""
    data = None
    if resume and path.exists():
        data = savefile.load(path)
        if not savefile.check(data, app.engine):
            data = None
    if data is not None:
        savefile.restore(data, app.engine, app.codes)
        print(f"前回の続きから再開します: {savefile.describe(app.engine, str(data.get('saved_at', '')))}")
    else:
        prev = savefile.back_up(path)
        print("最初から始めます" + (f"（前回の進行は {prev} に残しました）" if prev else ""))

    def save() -> None:
        try:
            savefile.save(path, app.engine, app.codes)
        except OSError as e:  # 保存できなくても進行は止めない
            print(f"進行を保存できませんでした: {e}")

    app.controller.on_change = save
    save()


def main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    try:
        engine = make_engine(args.teams, args.rehearsal)
    except ValueError as e:
        parser.error(str(e))
    hold = args.reveal_hold if args.reveal_hold is not None else BATTLE_REVEAL_HOLD
    app = BattleApp(engine, reveal_hold=hold)

    if args.save_file is not None:
        try:
            setup_save(app, args.save_file, args.resume)
        except ValueError as e:
            parser.error(f"保存された進行を読めません: {e}（最初から始めるときは --resume を付けない）")

    relay = None
    token = os.environ.get(ENV_HOST_TOKEN, "").strip()
    if args.offline:
        print("司会者画面（スマホ）: 無効（オフラインモード）")
    elif not token:
        print(f"司会者画面（スマホ）: 無効（環境変数 {ENV_HOST_TOKEN} が未設定）")
    else:
        base_url = http_base_from_cloud_host(resolve_cloud_host(args.cloud_host))
        relay = HostRelay(
            app.controller,
            HttpTransport(base_url, token),
            on_command=app.on_command,
            on_status=app.on_relay_status,
            build_state=app.build_state,
            execute=app.execute,
        )
        relay.start()
        print(f"司会者画面（スマホ）: {base_url}/host.html")
        print(f"回答ページ:           {base_url}/player.html")
        print(f"大画面:               {base_url}/board.html")
    if args.web:
        serve_local(
            app.controller,
            port=args.web_port,
            on_command=app.on_command,
            build_state=app.build_state,
            execute=app.execute,
        )
        print(f"司会者画面（予備）: http://localhost:{args.web_port}/host")
        print(f"回答ページ（手元）: http://localhost:{args.web_port}/player.html")
        print(f"大画面（手元）:     http://localhost:{args.web_port}/board.html")
    print("参加する部門:")
    app.print_teams()

    try:
        if args.headless:
            app.run_headless()
        else:
            app.run()
    finally:
        if relay:
            relay.stop()

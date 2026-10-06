"""司会進行用CLIダッシュボード。

使い方:
    uv run suzuleague                     # TurboWarp cloudに接続して進行
    uv run suzuleague --offline           # cloud接続なしでロジックのみ確認
    uv run suzuleague --project-id <ID>   # 接続先ルームの指定
    uv run suzuleague --web               # 司会者画面の予備を http://localhost:8000/host で開く
    uv run suzuleague --web --headless    # 入力を待たずに動かす（launcher のアプリから起動するとき）
    uv run suzuleague --save-file F --resume  # 操作のたびに F へ保存し、F の続きから始める

環境変数 SUZULEAGUE_HOST_TOKEN（合言葉）を設定しておくと、司会がスマホの
司会者画面（Render の /host.html）から進行を操作できる（host.py 参照）。

進行は next (n) で1段階ずつ進む。回答はScratch側からも、
answer <0-100> (a) でこちらからも入力できる。
"""

from __future__ import annotations

import argparse
import os
import signal
import threading
from pathlib import Path
import urllib.error
import urllib.request

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .cloud import ENV_CLOUD_HOST, CloudBridge, resolve_cloud_host, resolve_project_id
from .controller import GameController
from .engine import GameEngine, GameError, State
from .host import (
    ENV_HOST_TOKEN,
    HostRelay,
    HttpTransport,
    TeamCodes,
    http_base_from_cloud_host,
    serve_local,
)
from .labels import NEXT_HINTS, STATE_LABELS, team_status_label
from . import savefile
from .protocol import ACK_ANIMATION_DONE
from .questions import ROUNDS_PER_TEAM
from .teams import ENV_TEAMS, resolve_teams

HELP_TEXT = """\
コマンド一覧:
  next / n           進行を1段階進める
  answer <0-100> / a 回答を入力する（Scratch側からの回答の代行）
  status / s         現在の状況を表示
  teams / t          全チームのスコア一覧を表示
  resync             cloud変数の状態を再送（Scratch側リロード後など）
  help / h           このヘルプ
  quit / exit        終了\
"""


class Dashboard:
    def __init__(self, engine: GameEngine, bridge: CloudBridge | None) -> None:
        self.engine = engine
        self.bridge = bridge
        self.console = Console()
        self.player_codes = TeamCodes([t.number for t in engine.teams])
        self.controller = GameController(
            engine,
            bridge,
            on_push_error=lambda e: self.console.print(
                f"[red]cloud送信失敗: {e}（resyncで再送できます）[/]"
            ),
        )

    # ---- cloudイベント（イベントスレッドから呼ばれる） ----------

    def on_cloud_answer(self, percent: int) -> None:
        try:
            self.controller.answer(percent)
        except GameError as e:
            self.console.print(f"[yellow]Scratchからの回答を無視: {e}[/]")
            return
        self.console.print(
            f"[bold cyan]● Scratchから回答を受信: {percent}%[/] → next で正解発表"
        )

    def on_cloud_ack(self, code: int) -> None:
        if code == ACK_ANIMATION_DONE:
            self.console.print("[cyan]● Scratch: アニメーション完了[/]")
        else:
            self.console.print(f"[cyan]● Scratch: ACK({code})[/]")

    def on_host_command(self, command: dict, result: dict) -> None:
        mark = "[green]●[/]" if result["ok"] else "[yellow]●[/]"
        where = "出演者の回答画面" if command.get("source") == "player" else "司会者画面"
        self.console.print(f"{mark} {where}: {result['message']}")
        if result["ok"] and command.get("type") == "next":
            self.print_status()

    def on_relay_status(self, healthy: bool, detail: str) -> None:
        if healthy:
            self.console.print("[cyan]● 司会者画面（スマホ）と中継がつながりました[/]")
        else:
            self.console.print(
                f"[red]● 司会者画面（スマホ）との中継が切れました: {detail}[/]"
                "（自動で再接続します。急ぐときは --web の予備画面かCLIで操作）"
            )

    # ---- 表示 ----------------------------------------------------

    def print_status(self) -> None:
        engine = self.engine
        snap = engine.snapshot()
        team = engine.current_team
        question = engine.current_question

        lines = [f"[bold]ステート:[/] {STATE_LABELS[snap.state]}"]
        if team:
            fail = " [red](ゲームオーバー→エキシビション)[/]" if team.is_failed else ""
            lines.append(
                f"[bold]チーム:[/] {team.number} {team.name}"
                f"　[bold]バルーン:[/] {team.balloons}{fail}"
            )
            code = self.player_codes.for_team(team.number)
            lines.append(f"[bold]出演者の合言葉:[/] [bold cyan]{code}[/]（回答画面に入力してもらう）")
        if question:
            lines.append(f"[bold]第{snap.round_no}問 (ID:{question.id}):[/] {question.text}")
            lines.append(f"[bold]正解:[/] {question.correct}%（司会用・Scratchには発表時のみ送信）")
        result = engine.last_result
        if result and snap.state in (State.REVEAL, State.ROUND_RESULT):
            tag = "[magenta]エキシビション[/] " if result.exhibition else ""
            perfect = " [bold yellow]★ぴったり！[/]" if result.is_perfect else ""
            lines.append(
                f"{tag}回答 {result.answer}% / 正解 {result.correct}% "
                f"→ ダメージ {result.damage}{perfect}"
            )
        lines.append(f"[dim]▶ {NEXT_HINTS[snap.state]}[/]")
        conn = "オンライン" if self.bridge else "[yellow]オフライン[/]"
        self.console.print(Panel("\n".join(lines), title=f"スズリーグ ({conn})"))

    def print_teams(self) -> None:
        table = Table(title="チーム一覧")
        table.add_column("No.")
        table.add_column("チーム名")
        table.add_column("バルーン", justify="right")
        table.add_column("消化", justify="right")
        table.add_column("状態")
        colors = {"ゲームオーバー": "red", "クリア": "green"}
        for team in self.engine.teams:
            status = team_status_label(team)
            if status in colors:
                status = f"[{colors[status]}]{status}[/]"
            table.add_row(
                str(team.number),
                team.name,
                str(team.balloons),
                f"{team.finished_rounds}/{ROUNDS_PER_TEAM}",
                status,
            )
        self.console.print(table)
        if self.engine.state is State.FINISHED:
            winner = self.engine.winner()
            if winner:
                self.console.print(
                    f"[bold yellow]🏆 優勝: チーム{winner.number} {winner.name}"
                    f"（バルーン{winner.balloons}）[/]"
                )
            else:
                self.console.print("[red]クリアチームなし（優勝なし）[/]")

    # ---- 操作 ----------------------------------------------------

    def do_advance(self) -> None:
        try:
            self.controller.advance()
        except GameError as e:
            self.console.print(f"[yellow]{e}[/]")
            return
        self.print_status()
        if self.engine.state is State.FINISHED:
            self.print_teams()

    def do_answer(self, arg: str) -> None:
        try:
            percent = int(arg)
        except ValueError:
            self.console.print("[yellow]使い方: answer <0-100>[/]")
            return
        try:
            self.controller.answer(percent)
        except GameError as e:
            self.console.print(f"[yellow]{e}[/]")
            return
        self.console.print(f"回答 {percent}% を受け付けました → next で正解発表")

    def do_resync(self) -> None:
        try:
            self.controller.resync()
        except GameError as e:
            self.console.print(f"[yellow]{e}[/]")
            return
        self.console.print("状態を再送しました")

    # ---- メインループ --------------------------------------------

    def run(self) -> None:
        self.console.print("[bold]スズリーグ 司会ダッシュボード[/]（help でコマンド一覧）")
        self.print_status()
        while True:
            try:
                line = input("suzuleague> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            cmd, _, arg = line.partition(" ")
            cmd = cmd.lower()
            if cmd in ("quit", "exit"):
                break
            elif cmd in ("next", "n"):
                self.do_advance()
            elif cmd in ("answer", "a"):
                self.do_answer(arg.strip())
            elif cmd in ("status", "s"):
                self.print_status()
            elif cmd in ("teams", "t"):
                self.print_teams()
            elif cmd == "resync":
                self.do_resync()
            elif cmd in ("help", "h"):
                self.console.print(HELP_TEXT)
            else:
                self.console.print(f"[yellow]不明なコマンド: {cmd}（help参照）[/]")
        self.shutdown()

    def run_headless(self) -> None:
        """入力を待たずに動かし続け、SIGTERM / SIGINT で終える。

        launcher のアプリはターミナルを開かずに起動するので、操作はすべて
        司会者画面から行う（Scratch への送り直しも画面のボタンでできる）。
        """
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        self.console.print("[bold]スズリーグ 司会ダッシュボード[/]（ターミナルなし。操作は司会者画面から）")
        self.print_status()
        while not stop.wait(1.0):
            pass
        self.shutdown()

    def shutdown(self) -> None:
        if self.bridge:
            self.bridge.disconnect()
        self.console.print("終了しました")


def main() -> None:
    parser = argparse.ArgumentParser(description="スズリーグ 司会進行ダッシュボード")
    parser.add_argument("--project-id", default=None, help="cloudサーバのルームID")
    parser.add_argument("--offline", action="store_true", help="cloud接続なしで起動")
    parser.add_argument(
        "--cloud-host",
        default=None,
        help=f"接続先cloudサーバ (環境変数 {ENV_CLOUD_HOST} でも指定可。既定は公開サーバ)",
    )
    parser.add_argument(
        "--teams",
        default=None,
        help=f"チーム構成JSONのパス (環境変数 {ENV_TEAMS} でも指定可。書式は teams.example.json)",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="司会者画面を裏方PC上でも開く（http://localhost:8000/host。Renderに届かないときの予備）",
    )
    parser.add_argument("--web-port", type=int, default=8000, help="--web の待ち受けポート")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="コマンド入力を待たずに動かす（SIGTERM で終了。launcher のアプリ用）",
    )
    parser.add_argument(
        "--save-file",
        type=Path,
        default=None,
        help="操作のたびに進行を保存するファイル（裏方PCを起動し直したときに --resume で続きから始める）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="--save-file の続きから始める（付けなければ最初から。前回の保存は .prev に残す）",
    )
    parser.add_argument(
        "--perfect-bonus",
        type=int,
        default=0,
        help="ぴったり賞のボーナスバルーン数（本番は不採用。指定しないこと）",
    )
    args = parser.parse_args()

    if args.resume and args.save_file is None:
        parser.error("--resume には --save-file が必要です")
    try:
        teams = resolve_teams(args.teams)
    except ValueError as e:
        parser.error(str(e))

    engine = GameEngine(teams=teams, perfect_bonus=args.perfect_bonus)
    bridge = None
    holder: list[Dashboard] = []  # cloud の受信コールバックからダッシュボードを参照する
    if not args.offline:
        project_id = resolve_project_id(args.project_id)
        try:
            bridge = CloudBridge(
                project_id,
                cloud_host=args.cloud_host,
                on_answer=lambda pct: holder[0].on_cloud_answer(pct),
                on_ack=lambda code: holder[0].on_cloud_ack(code),
            )
        except ValueError as e:
            parser.error(str(e))
    dashboard = Dashboard(engine, bridge)
    holder.append(dashboard)

    # 続きから始めるなら、cloud へ最初の状態を送る前に読み戻す（読み戻した状態をそのまま配る）
    if args.save_file is not None:
        try:
            setup_save(dashboard, args.save_file, args.resume)
        except ValueError as e:
            parser.error(f"保存された進行を読めません: {e}（最初から始めるときは --resume を付けない）")

    if bridge is not None:
        print(f"cloudサーバに接続中... (project_id={project_id})")
        print(f"  接続先: {bridge.cloud_host}")
        connect_with_wake(bridge)
        bridge.push(engine.snapshot())  # 初期状態を送信

    relay = start_host_relay(dashboard, None if args.offline else args.cloud_host)
    if args.web:
        serve_local(
            dashboard.controller,
            port=args.web_port,
            on_command=dashboard.on_host_command,
            player_codes=dashboard.player_codes,
        )
        print(f"司会者画面（予備）: http://localhost:{args.web_port}/host")

    try:
        if args.headless:
            dashboard.run_headless()
        else:
            dashboard.run()
    finally:
        if relay:
            relay.stop()


def setup_save(dashboard: Dashboard, path: Path, resume: bool) -> None:
    """続きから始めるなら保存を読み戻し、以降は操作のたびに保存する（#51）。

    保存が今のチーム構成・問題で読めなければ ValueError（エンジンには触らない）。
    """
    engine = dashboard.engine
    data = None
    if resume and path.exists():
        data = savefile.load(path)
        if not savefile.check(data, engine):  # 始まる前か終わった後。続きから始める意味がない
            data = None
    if data is not None:
        savefile.restore(data, engine, dashboard.player_codes)
        print(f"前回の続きから再開します: {savefile.describe(engine, str(data.get('saved_at', '')))}")
    else:
        prev = savefile.back_up(path)
        print("最初から始めます" + (f"（前回の進行は {prev} に残しました）" if prev else ""))

    def save() -> None:
        try:
            savefile.save(path, engine, dashboard.player_codes)
        except OSError as e:  # 保存できなくても進行は止めない
            dashboard.console.print(f"[red]進行を保存できませんでした: {e}[/]")

    dashboard.controller.on_change = save
    save()


def wake_server(base_url: str, timeout: float = 90.0) -> bool:
    """寝ているサーバを起こす。HTTPで1回開けば起き上がる（Render 無料枠は復帰に数十秒）。"""
    try:
        with urllib.request.urlopen(base_url + "/", timeout=timeout) as res:
            res.read(1)
        return True
    except (urllib.error.URLError, OSError):
        return False


def connect_with_wake(bridge: CloudBridge, attempts: int = 3) -> None:
    """接続に失敗したらサーバを起こしてやり直す。

    Render 無料枠は15分無通信で眠り、眠っているとWebSocketの接続が
    タイムアウトする（scratchattach の待ち時間は3秒）。開演前に起こし忘れても
    ダッシュボードが落ちないよう、起こしてから接続し直す。
    """
    base_url = http_base_from_cloud_host(bridge.cloud_host)
    for attempt in range(1, attempts + 1):
        try:
            bridge.connect()
            return
        except Exception as e:  # scratchattach は接続失敗で様々な例外を投げる
            if attempt == attempts:
                raise
            print(f"  接続できませんでした（{type(e).__name__}）。サーバを起こしています…（最大90秒）")
            woke = wake_server(base_url)
            print("  サーバが応答しました。接続し直します" if woke else "  サーバの応答がありません。もう一度試します")


def start_host_relay(dashboard: Dashboard, cloud_host: str | None) -> HostRelay | None:
    """合言葉が設定されていれば、Render 経由の司会者画面を有効にする。"""
    token = os.environ.get(ENV_HOST_TOKEN, "").strip()
    if not token:
        print(f"司会者画面（スマホ）: 無効（環境変数 {ENV_HOST_TOKEN} が未設定）")
        return None
    if dashboard.bridge is None:
        print("司会者画面（スマホ）: 無効（オフラインモード）")
        return None
    base_url = http_base_from_cloud_host(resolve_cloud_host(cloud_host))
    relay = HostRelay(
        dashboard.controller,
        HttpTransport(base_url, token),
        on_command=dashboard.on_host_command,
        on_status=dashboard.on_relay_status,
        player_codes=dashboard.player_codes,
    )
    relay.start()
    print(f"司会者画面（スマホ）: {base_url}/host.html")
    print(f"出演者の回答画面:     {base_url}/player.html")
    return relay


if __name__ == "__main__":
    main()

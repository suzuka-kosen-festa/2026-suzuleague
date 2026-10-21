"""本番と同じ構成で全部の画面をブラウザで開き、4チーム20問を最後まで通して確かめる。

`tools/stage_check.py` はステージ画面だけを見る。こちらは、司会者画面の「次へ」で進め、
観客ページ・予備の回答画面・ステージ画面（iPad と大画面）が、裏方PCの進行どおりに動くかを見る。

- 隣の cloud-server（`../cloud-server`）を手元で起動し（合言葉つき）、裏方PCのダッシュボードを
  そこにつないで起動する。本番のサーバとランキングには触れない
- 画面はすべて WebKit（Safari と同じ）で開く。司会と観客は iPhone、出演者用は iPad（横向き・タッチ）
- 進め方は本番と同じ。司会者画面の「次へ」だけで進め、出演者の答えは、各チームの1問目は iPad のテンキー、
  チーム1の2問目は予備の回答画面（4桁の合言葉）、ほかは司会者画面の代理入力で入れる
- チーム2は2問目までに大きく外してゲームオーバーにし、残りはエキシビションで進める
- 同じ進行エンジンを手元でも動かし、司会者画面の残りバルーン・ステージ画面の値と突き合わせる
- 正解発表のあと15秒は「次へ」が押せず、観客のスマホにも正解が出ないことを見る
- チーム3の3問目の回答受付で裏方PCのダッシュボードを止め、「続きから」で再開できることを見る
- 最後に、全体結果（優勝・観客ランキング）が司会者画面・観客ページ・ステージ画面にそろって出るかを見る

    # 初回だけ: WebKit を入れる
    uv run --with playwright python -m playwright install webkit

    uv run --with playwright python tools/system_check.py            # 約15分
    uv run --with playwright python tools/system_check.py --headed   # ブラウザを見ながら
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stage_check import IPAD, SNAPSHOT_JS, TOUCH_JS, VIEWPORT, Report, _free_port, _wait_until, cloud_server  # noqa: E402

from suzuleague.controller import MIN_ADVANCE_INTERVAL  # noqa: E402
from suzuleague.engine import GameEngine, State  # noqa: E402
from suzuleague.host import ENV_HOST_TOKEN  # noqa: E402
from suzuleague.protocol import REVEAL_SECONDS  # noqa: E402
from suzuleague.publish import DEFAULT_SERVER_DIR  # noqa: E402
from suzuleague.stage import SCRATCH_PROJECT_ID  # noqa: E402
from suzuleague.teams import load_teams  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
TOKEN = "system-check-token"  # 手元のサーバだけで使う合言葉
PHONES = ("iPhone 17 Pro", "iPhone 17e")  # 司会と観客のスマホ（Playwright の端末の設定の名前）
AUDIENCE = ("観客A", "観客B")
RESTART_AT = (3, 3)  # このチーム・問題の回答受付で、裏方PCのダッシュボードを止めて「続きから」で再開する


def stage_answer(team: int, round_no: int, correct: int) -> int:
    """出演者の答え。チーム2は2問目までで大きく外してゲームオーバーにし、チーム1の5問目はぴったりにする。"""
    if team == 2 and round_no <= 2:
        return 0 if correct >= 50 else 100  # どちらも50以上ずれる → 2問でバルーン100個がなくなる
    if team == 1 and round_no == 5:
        return correct
    return min(100, correct + 2) if correct <= 50 else correct - 2


def answer_route(team: int, round_no: int) -> str:
    if round_no == 1:
        return "ipad"  # 各チームの1問目（効果音の途中で回答受付に進めてもテンキーが出るか）
    if (team, round_no) == (1, 2):
        return "player"
    return "proxy"


# ---- 裏方PCのダッシュボード -----------------------------------------


class Dashboard:
    """裏方PCのダッシュボードを、手元のサーバにつないで別のプロセスで動かす（アプリの「本番」と同じ動かし方）。"""

    def __init__(self, port: int, teams: Path, work: Path) -> None:
        self.args = [
            "--cloud-host", f"ws://localhost:{port}",
            "--project-id", SCRATCH_PROJECT_ID,
            "--teams", str(teams),
            "--headless",
            "--save-file", str(work / "game.json"),
        ]
        self.log = (work / "dashboard.log").open("a")
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self, *, resume: bool = False) -> None:
        cmd = [sys.executable, "-m", "suzuleague.dashboard", *self.args, *(["--resume"] if resume else [])]
        env = os.environ | {ENV_HOST_TOKEN: TOKEN, "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=self.log, stderr=subprocess.STDOUT)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---- 画面 ------------------------------------------------------------


class Pages:
    def __init__(self, browser: Any, devices: dict[str, Any], base: str) -> None:
        phone = devices[PHONES[0]]
        self.host = browser.new_context(**phone).new_page()
        self.audience = [browser.new_context(**devices[name]).new_page() for name in PHONES]
        self.player = browser.new_context(**devices[PHONES[1]]).new_page()
        self.ipad = browser.new_context(**devices[IPAD]).new_page()
        self.view = browser.new_page(viewport=VIEWPORT)
        self.base = base

    # 司会者画面
    def open_host(self) -> None:
        self.host.goto(f"{self.base}/host.html")
        self.host.locator("#token-input").wait_for(state="visible", timeout=15000)
        self.host.fill("#token-input", TOKEN)
        self.host.press("#token-input", "Enter")

    def host_text(self, element_id: str) -> str:
        return self.host.locator(f"#{element_id}").inner_text().strip()

    def next_enabled(self) -> bool:
        return self.host.evaluate("() => !document.getElementById('next-btn').disabled")

    def press_next(self) -> None:
        self.host.locator("#next-btn").tap()

    # 観客ページ
    def open_audience(self) -> None:
        for page, name in zip(self.audience, AUDIENCE, strict=True):
            page.goto(f"{self.base}/suzuleague.html")
            page.locator("#join-name").wait_for(state="visible", timeout=15000)
            page.fill("#join-name", name)
            page.press("#join-name", "Enter")

    # ステージ画面（iPad はタップ、大画面はクリックで始める）
    def open_stage(self) -> None:
        for page, path, touch in ((self.ipad, "play", True), (self.view, "view", False)):
            page.goto(f"{self.base}/stage/{path}/")
            page.wait_for_function("() => window.vm && vm.runtime && vm.runtime.targets.length > 5", timeout=60000)
            time.sleep(1.5)
            size = page.viewport_size or VIEWPORT
            if touch:
                page.touchscreen.tap(size["width"] / 2, size["height"] / 2)
            else:
                page.mouse.click(size["width"] / 2, size["height"] / 2)
            page.wait_for_function("() => vm.runtime.threads.length > 0", timeout=10000)

    def stage_var(self, page: Any, name: str) -> str:
        return page.evaluate(
            "(name) => String(Object.values(vm.runtime.getTargetForStage().variables).find(v => v.name === name)?.value)",
            f"☁ {name}",
        )

    def tap_stage(self, x: float, y: float) -> None:
        box = self.ipad.locator("canvas").first.bounding_box()
        px, py = box["x"] + (x + 240) / 480 * box["width"], box["y"] + (180 - y) / 360 * box["height"]
        self.ipad.evaluate(TOUCH_JS, [px, py, 150])

    def shown(self, sprite: str, costume: str | None = None) -> list[dict[str, Any]]:
        snap = self.ipad.evaluate(SNAPSHOT_JS)
        return [p for p in snap["parts"] if p["name"] == sprite and p["shown"] and (costume is None or p["costume"] == costume)]


# ---- 通し ------------------------------------------------------------


class Run:
    def __init__(self, pages: Pages, dashboard: Dashboard, teams_file: Path) -> None:
        self.p = pages
        self.dashboard = dashboard
        self.report = Report()
        self.engine = GameEngine(teams=load_teams(teams_file))  # 答え合わせ用に、同じ進行を手元でも動かす
        self.reveal_delays: list[float] = []
        self.last_press = 0.0

    def wait_next(self, timeout: float = 60) -> bool:
        return _wait_until(self.p.next_enabled, timeout, step=0.3)

    def run(self) -> None:
        p = self.p
        p.open_host()
        self.report.check(self.wait_next(30), "司会者画面が裏方PCとつながり「次へ」が押せる", p.host_text("next-sub"))
        p.open_audience()
        p.open_stage()
        while self.engine.state is not State.FINISHED:
            self.step()
        self.check_final()

    def step(self) -> None:
        p, engine = self.p, self.engine
        before = engine.state
        if before in (State.ANSWERING, State.EXHIBITION_ANSWERING):
            self.answer()
        if not self.wait_next(REVEAL_SECONDS + 30):
            self.report.check(False, f"{self.where()} で「次へ」が押せる", p.host_text("next-sub"))
            raise SystemExit("「次へ」が押せないまま止まりました")
        # 前の「次へ」から1秒以内の「次へ」は、押し間違いとして取り消される（司会も続けては押さない）
        time.sleep(max(0.0, self.last_press + MIN_ADVANCE_INTERVAL + 0.5 - time.monotonic()))
        p.press_next()
        self.last_press = time.monotonic()
        engine.advance()
        _wait_until(lambda: p.host_text("state") == STATE_TEXT[engine.state], 10)
        self.report.check(
            p.host_text("state") == STATE_TEXT[engine.state],
            f"{self.where()}: 司会者画面が「{STATE_TEXT[engine.state]}」になる",
            p.host_text("state"),
        )
        if engine.state is State.REVEAL:
            self.check_reveal()
        if engine.state in (State.QUESTION, State.REVEAL, State.ROUND_RESULT):
            self.check_numbers()

    def where(self) -> str:
        snap = self.engine.snapshot()
        return f"チーム{snap.team_no} {snap.round_no}問目"

    # -- 答える --

    def answer(self) -> None:
        p, snap = self.p, self.engine.snapshot()
        q = self.engine.current_question
        assert q is not None
        answer = stage_answer(snap.team_no, snap.round_no, q.correct)
        route = answer_route(snap.team_no, snap.round_no)
        title = self.where()
        # 観客2人も同時に答える（A は正解の近く、B は 50 のまま）
        for page, value in zip(p.audience, (min(100, q.correct + 5), 50), strict=True):
            ok = _wait_until(lambda page=page: page.locator("#view-answer").is_visible(), 10)
            if ok:
                page.evaluate(
                    "(v) => { const s = document.getElementById('slider'); s.value = v; s.dispatchEvent(new Event('input', {bubbles: true})); }",
                    value,
                )
                page.locator("#lock-btn").tap()
        if route == "ipad":
            self.answer_on_ipad(title, answer)
        elif route == "player":
            self.answer_on_player(title, answer)
        else:
            p.host.fill("#proxy-input", str(answer))
            p.host.locator("#proxy-btn").tap()
        ok = _wait_until(lambda: str(answer) in p.host_text("answer-now"), 10)
        self.report.check(ok, f"{title}: {route} で入れた回答 {answer} が司会者画面に届く", p.host_text("answer-now"))
        self.engine.submit_answer(answer)
        if (snap.team_no, snap.round_no) == RESTART_AT:
            self.restart_dashboard(title, answer)

    def answer_on_ipad(self, title: str, answer: int) -> None:
        ok = _wait_until(lambda: bool(self.p.shown("キー")), 10)
        self.report.check(ok, f"{title}: iPad にテンキーが出る")
        for digit in str(answer):
            keys = self.p.shown("キー", digit)
            if keys:
                self.p.tap_stage(keys[0]["x"], keys[0]["y"])
                time.sleep(0.4)
        go = self.p.shown("決定")
        if go:
            self.p.tap_stage(go[0]["x"], go[0]["y"])

    def answer_on_player(self, title: str, answer: int) -> None:
        p = self.p
        code = p.host_text("player-code")
        self.report.check(code.isdigit() and len(code) == 4, f"{title}: 司会者画面に4桁の合言葉が出る", code)
        p.player.goto(f"{p.base}/player.html")
        p.player.locator("#code-input").wait_for(state="visible", timeout=15000)
        p.player.fill("#code-input", code)
        p.player.press("#code-input", "Enter")
        ok = _wait_until(lambda: p.player.locator("#view-answer").is_visible(), 10)
        self.report.check(ok, f"{title}: 予備の回答画面に合言葉で入れる")
        for digit in str(answer):
            p.player.locator(f"#keys [data-key='{digit}']").tap()
        p.player.locator("#send-btn").tap()

    def restart_dashboard(self, title: str, answer: int) -> None:
        """裏方PCのダッシュボードを止め、司会者画面に応答なしと出るのを見てから「続きから」で再開する。"""
        p = self.p
        self.dashboard.stop()
        down = _wait_until(lambda: not p.next_enabled() and "応答なし" in p.host_text("conn-text"), 20)
        self.report.check(
            down, f"{title}: 裏方PCを止めると、司会者画面に応答なしと出て「次へ」が押せなくなる",
            f"{p.host_text('conn-text')}・{p.host_text('next-sub')}",
        )
        self.dashboard.start(resume=True)
        back = _wait_until(
            lambda: "接続中" in p.host_text("conn-text") and p.next_enabled() and str(answer) in p.host_text("answer-now"), 60
        )
        self.report.check(back, f"{title}: 「続きから」で、届いていた回答 {answer} ごと再開する", p.host_text("answer-now"))

    # -- 照合 --

    def check_reveal(self) -> None:
        """正解発表のあと15秒は、「次へ」が押せず、観客のスマホにも正解が出ない。"""
        p, title = self.p, self.where()
        result = self.engine.last_result
        assert result is not None
        started = time.monotonic()
        held = not p.next_enabled() and "演出中" in p.host_text("next-sub")
        self.report.check(held, f"{title}: 正解発表の直後は「次へ」が押せず、残り秒数が出る", p.host_text("next-sub"))
        audience = p.audience[0]
        shown = _wait_until(lambda: audience.locator("#view-reveal").is_visible(), REVEAL_SECONDS + 10, step=0.25)
        delay = time.monotonic() - started
        self.reveal_delays.append(delay)
        self.report.check(
            shown and delay >= REVEAL_SECONDS - 1.5,
            f"{title}: 観客のスマホは、正解発表から約{REVEAL_SECONDS}秒たってから正解を出す",
            f"{delay:.1f}秒",
        )
        correct = audience.locator("#r-correct").inner_text()
        self.report.check(str(result.correct) in correct, f"{title}: 観客のスマホの正解が {result.correct}%", correct)

    def check_numbers(self) -> None:
        p, snap, title = self.p, self.engine.snapshot(), self.where()
        balloons = p.host_text("balloons")
        self.report.check(str(snap.balloons) in balloons, f"{title}: 司会者画面の残りバルーンが {snap.balloons}", balloons)
        for page, label in ((p.ipad, "iPad"), (p.view, "大画面")):
            ok = _wait_until(lambda page=page: self.p.stage_var(page, "P2S_BALLOONS") == str(snap.balloons)
                             and self.p.stage_var(page, "P2S_QID") == str(snap.question_id), 10)
            self.report.check(ok, f"{title}: {label}の問題番号と残りバルーンが裏方PCと合う",
                              f"{self.p.stage_var(page, 'P2S_QID')}・{self.p.stage_var(page, 'P2S_BALLOONS')}")

    def check_final(self) -> None:
        p = self.p
        winners = [t.name for t in self.engine.winners()]
        _wait_until(lambda: p.host.locator("#winner-card").is_visible(), 10)
        host_winner = p.host_text("winner-name")
        self.report.check(all(w in host_winner for w in winners), f"全体結果: 司会者画面の優勝が {'・'.join(winners)}", host_winner)
        for page, name in zip(p.audience, AUDIENCE, strict=True):
            ok = _wait_until(lambda page=page: page.locator("#view-final").is_visible(), 20)
            self.report.check(ok, f"全体結果: {name}のスマホに全体結果が出る")
            if not ok:
                continue
            winner = page.locator("#f-winner").inner_text()
            self.report.check(all(w in winner for w in winners), f"全体結果: {name}のスマホの優勝が {'・'.join(winners)}", winner)
            board = page.locator("#f-audience").inner_text() if page.locator("#f-audience-title").is_visible() else ""
            self.report.check(all(n in board for n in AUDIENCE), f"全体結果: {name}のスマホの観客ランキングに2人とも出る", board.replace("\n", " "))
        # 司会者画面の観客ランキングは、たたんだ欄を開いたときだけ読み込む
        p.host.locator("#ranking-card > summary").tap()
        _wait_until(lambda: all(n in p.host_text("ranking") for n in AUDIENCE), 10)
        ranking = p.host_text("ranking")
        self.report.check(all(n in ranking for n in AUDIENCE), "全体結果: 司会者画面の観客ランキングに2人とも出る", ranking.replace("\n", " "))
        for page, label in ((p.ipad, "iPad"), (p.view, "大画面")):
            ok = _wait_until(lambda page=page: any(
                x["name"] == "結果発表" and x["shown"] for x in page.evaluate(SNAPSHOT_JS)["parts"]), 15)
            self.report.check(ok, f"全体結果: {label}に結果発表の画面が出る")


STATE_TEXT = {
    State.IDLE: "待機",
    State.TEAM_INTRO: "チーム紹介",
    State.QUESTION: "出題",
    State.ANSWERING: "回答受付",
    State.REVEAL: "正解発表",
    State.ROUND_RESULT: "ラウンド結果",
    State.TEAM_RESULT: "チーム結果",
    State.EXHIBITION_ANSWERING: "回答受付(エキシビション)",
    State.FINISHED: "全体結果",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本番と同じ構成で全部の画面を開き、4チーム20問を通して確かめる")
    parser.add_argument("--server-dir", type=Path, default=DEFAULT_SERVER_DIR, help="cloud-server のクローン先")
    parser.add_argument("--teams", type=Path, default=REPO / "teams.json", help="チーム構成（既定はアプリの「本番」と同じ teams.json）")
    parser.add_argument("--headed", action="store_true", help="ブラウザを見えるように開く")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    port = _free_port()
    os.environ["HOST_TOKEN"] = TOKEN  # 手元の cloud-server の司会者画面を有効にする
    with tempfile.TemporaryDirectory() as tmp, cloud_server(args.server_dir, port), sync_playwright() as pw:
        work = Path(tmp)
        dashboard = Dashboard(port, args.teams, work)
        dashboard.start()
        browser = pw.webkit.launch(headless=not args.headed)
        run = Run(Pages(browser, pw.devices, f"http://localhost:{port}"), dashboard, args.teams)
        try:
            run.run()
        except SystemExit as e:
            print(f"\n中断: {e}")
        finally:
            browser.close()
            dashboard.stop()
            log = (work / "dashboard.log").read_text()
        report = run.report
        if report.failures:
            print("\n--- 裏方PCのログ（末尾） ---\n" + "\n".join(log.splitlines()[-30:]))
    if run.reveal_delays:
        print(f"\n観客のスマホに正解が出るまで: {min(run.reveal_delays):.1f}〜{max(run.reveal_delays):.1f}秒（{len(run.reveal_delays)}問）")
    print(f"\n{report.passed} 件 OK、{len(report.failures)} 件 NG")
    for failure in report.failures:
        print(f"  ✗ {failure}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())

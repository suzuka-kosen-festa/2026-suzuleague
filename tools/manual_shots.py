"""使い方マニュアル（docs/manual/）に載せる画面を、手元で4チーム20問を流しながら撮る。

`tools/system_check.py` と同じく、隣の cloud-server を手元で起動して裏方PCのダッシュボードをつなぎ、
司会者画面の「次へ」で本番どおりに進める。本番のサーバとランキングには触れない。
撮った画像（PNG）と、画像の中の部品の位置（shots.json）を、指定したフォルダに書き出す。
マニュアルの PDF は `tools/manual_pdf.py` がこのフォルダを読んで作る。

- 画面は Chromium で開く（ステージ画面の絵を撮れて、PDF も同じブラウザで作れるため）。
  司会・観客・予備の回答画面は iPhone、出演者用は iPad（横向き）、大画面用は 1280×720
- チーム2は2問目までにゲームオーバーにして、エキシビションとクリア失敗の画面も撮る
- チーム3の3問目の回答受付で裏方PCを止め、「PCの応答なし」の画面を撮ってから「続きから」で戻す
- マニュアルは出演者も読むので、本番の問題と正解は写さない。裏方PCと観客ページの問題は練習用の例題
  （SAMPLE_QUESTIONS）に差し替える。ステージ画面の問題文は絵なので差し替えられず、PDF にするときに隠す

    uv run --with "playwright==1.56.0" python tools/manual_shots.py <書き出し先のフォルダ>   # 約12分
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stage_check import BROWSER_ARGS, TOUCH_JS, _free_port, _wait_until, cloud_server  # noqa: E402
from system_check import STATE_TEXT, TOKEN, Dashboard, stage_answer  # noqa: E402

from suzuleague.controller import MIN_ADVANCE_INTERVAL  # noqa: E402
from suzuleague.engine import GameEngine, State  # noqa: E402
from suzuleague.host import ENV_HOST_TOKEN  # noqa: E402
from suzuleague.models import Question  # noqa: E402
from suzuleague.protocol import REVEAL_SECONDS  # noqa: E402
from suzuleague.publish import DEFAULT_SERVER_DIR  # noqa: E402
from suzuleague.questions import QuestionSet  # noqa: E402
from suzuleague.teams import load_teams  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PHONE = "iPhone 15 Pro"  # Playwright 1.56 の端末の設定の名前
IPAD = "iPad (gen 11) landscape"
VIEW = {"width": 1280, "height": 720}
AUDIENCE = ("すずか", "こうせん")

SAMPLE_SOURCE = "練習用の例題（本番の問題ではありません）"
SAMPLE_QUESTIONS = [
    Question(i, text, correct, SAMPLE_SOURCE)
    for i, (text, correct) in enumerate(
        [
            ("朝ごはんを毎日食べる人は何パーセント？", 64),
            ("スマホを1日5時間以上使う人は何パーセント？", 38),
            ("カレーは甘口派の人は何パーセント？", 27),
            ("犬より猫が好きな人は何パーセント？", 46),
            ("きのこの山派の人は何パーセント？", 52),
            ("朝型だと答えた人は何パーセント？", 35),
            ("毎日自炊する人は何パーセント？", 12),
            ("ゲームを毎日する人は何パーセント？", 58),
            ("傘をよく忘れる人は何パーセント？", 41),
            ("好きな季節が冬の人は何パーセント？", 22),
            ("アイスはカップ派の人は何パーセント？", 61),
            ("夜10時前に寝る人は何パーセント？", 9),
            ("自転車で通学する人は何パーセント？", 33),
            ("目玉焼きにしょうゆをかける人は何パーセント？", 55),
            ("カラオケが好きな人は何パーセント？", 72),
            ("ラーメンはしょうゆ派の人は何パーセント？", 30),
            ("文化祭が楽しみな人は何パーセント？", 85),
            ("毎日日記を書く人は何パーセント？", 6),
            ("宿題は早めに終わらせる派の人は何パーセント？", 24),
            ("海より山が好きな人は何パーセント？", 43),
        ],
        start=1,
    )
]

# 裏方PCのダッシュボードを、問題だけ例題に差し替えて動かす
DASHBOARD_WITH_SAMPLES = """
import json, sys
from suzuleague import questions
from suzuleague.models import Question
questions.PRODUCTION_QUESTIONS[:] = [Question(*q) for q in json.loads(sys.argv[1])]
sys.argv = ["suzuleague.dashboard", *sys.argv[2:]]
from suzuleague.dashboard import main
main()
"""


class SampleDashboard(Dashboard):
    def start(self, *, resume: bool = False) -> None:
        samples = json.dumps([[q.id, q.text, q.correct, q.source] for q in SAMPLE_QUESTIONS], ensure_ascii=False)
        cmd = [sys.executable, "-c", DASHBOARD_WITH_SAMPLES, samples, *self.args, *(["--resume"] if resume else [])]
        env = os.environ | {ENV_HOST_TOKEN: TOKEN, "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=self.log, stderr=subprocess.STDOUT)


def sample_audience_page(route: Any) -> None:
    """観客ページに埋め込まれた問題文を、例題に差し替えて返す。"""
    body = route.fetch().text()
    table = json.dumps({str(q.id): q.text for q in SAMPLE_QUESTIONS}, ensure_ascii=False)
    body, n = re.subn(r"var QUESTIONS = \{.*?\};", lambda _: f"var QUESTIONS = {table};", body, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("観客ページの問題文の埋め込みが見つかりません")
    route.fulfill(body=body, content_type="text/html; charset=utf-8")

# 画面の中の部品の位置を、画面の幅・高さに対する割合（%）で返す
DOM_BOXES_JS = """(selectors) => {
  const out = {};
  for (const [key, sel] of Object.entries(selectors)) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    out[key] = [r.left / innerWidth * 100, r.top / innerHeight * 100, r.width / innerWidth * 100, r.height / innerHeight * 100];
  }
  return out;
}"""

# ステージ画面で見えている部品（スプライト）の位置
STAGE_BOXES_JS = """() => {
  const c = document.querySelector('canvas');
  const r = c.getBoundingClientRect();
  const out = [];
  for (const t of vm.runtime.targets) {
    if (t.isStage || !t.visible || (t.effects.ghost || 0) >= 100) continue;
    const b = vm.renderer.getBounds(t.drawableID);
    if (!b) continue;
    const x = r.left + (b.left + 240) / 480 * r.width, y = r.top + (180 - b.top) / 360 * r.height;
    out.push({
      name: t.sprite.name, costume: t.getCostumes()[t.currentCostume].name,
      box: [x / innerWidth * 100, y / innerHeight * 100,
            (b.right - b.left) / 480 * r.width / innerWidth * 100, (b.top - b.bottom) / 360 * r.height / innerHeight * 100],
    });
  }
  return out;
}"""

# 部品の下の端が、画面の下に固定された「次へ」の帯より上に来るまでずらす（上の端は画面から出さない）
SCROLL_ABOVE_DOCK_JS = """(el) => {
  const dock = document.querySelector('.dock');
  const limit = (dock ? dock.getBoundingClientRect().top : innerHeight) - 12;
  const r = el.getBoundingClientRect();
  if (r.bottom > limit) window.scrollBy(0, Math.min(r.bottom - limit, r.top - 8));
}"""

# 司会者画面の部品（マニュアルの注釈で指す名前 → CSS セレクタ）
HOST_PARTS = {
    "conn": "#conn", "state": "#state", "code": "#code-box", "team": "#team-row", "balloons": "#balloons",
    "question": "#question-card", "reveal": "#reveal-btn", "answer": "#answer-card", "answer-now": "#answer-now",
    "proxy": ".proxy", "proxy-input": "#proxy-input", "proxy-btn": "#proxy-btn", "result": "#result-card",
    "winner": "#winner-card", "teams": "#teams-card", "ranking": "#ranking-card", "ranking-reset": "#ranking-reset",
    "trouble": "#trouble-card", "resync": "#resync-btn", "toast": "#toast", "next": "#next-btn", "next-sub": "#next-sub",
    "dock": ".dock", "token": "#token-form", "token-input": "#token-input", "token-btn": "#token-form button",
}
PLAYER_PARTS = {
    "code-form": "#view-code", "code-input": "#code-input", "team": "#team-card", "question": "#a-question",
    "readout": "#readout", "keys": "#keys", "send": "#send-btn", "send-status": "#send-status", "result": "#view-result",
    "message": "#view-message",
}
AUDIENCE_PARTS = {"join": "#join", "join-name": "#join-name", "slider": "#slider", "lock": "#lock-btn", "final": "#view-final"}


class Shots:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.meta: dict[str, Any] = {}
        out.mkdir(parents=True, exist_ok=True)

    def take(self, page: Any, name: str, parts: dict[str, str] | None = None, stage: bool = False) -> None:
        for attempt in range(3):  # 演出の重い画面では、撮るのがまれに時間切れになる
            try:
                page.screenshot(path=str(self.out / f"{name}.png"), timeout=60000)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1)
        size = page.viewport_size
        entry: dict[str, Any] = {"w": size["width"], "h": size["height"]}
        if parts:
            entry["boxes"] = page.evaluate(DOM_BOXES_JS, parts)
        if stage:
            entry["sprites"] = page.evaluate(STAGE_BOXES_JS)
        self.meta[name] = entry
        (self.out / "shots.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=1))
        print(f"  📷 {name}")


class Capture:
    def __init__(self, pw: Any, browser: Any, base: str, shots: Shots, dashboard: Dashboard, teams: Path) -> None:
        phone = pw.devices[PHONE] | {"device_scale_factor": 2}
        self.host = browser.new_context(**phone).new_page()
        self.audience = [browser.new_context(**phone).new_page() for _ in AUDIENCE]
        for page in self.audience:
            page.route("**/suzuleague.html", sample_audience_page)
        self.player = browser.new_context(**phone).new_page()
        self.play = browser.new_context(**pw.devices[IPAD] | {"device_scale_factor": 2}).new_page()
        self.view = browser.new_context(viewport=VIEW).new_page()
        self.base, self.s, self.dashboard = base, shots, dashboard
        self.engine = GameEngine(teams=load_teams(teams), question_set=QuestionSet(SAMPLE_QUESTIONS))  # 同じ進行を手元でも動かす
        self.last_press = 0.0

    # ---- 撮る ----

    def host_shot(self, name: str, scroll_to: str | None = None) -> None:
        if scroll_to:  # 下の「次へ」の帯に隠れないところまでずらす
            self.host.locator(scroll_to).evaluate(SCROLL_ABOVE_DOCK_JS)
            time.sleep(0.4)
        else:
            self.host.evaluate("() => window.scrollTo(0, 0)")
        self.s.take(self.host, name, HOST_PARTS)

    def stage_shot(self, name: str, view_too: bool = False) -> None:
        self.s.take(self.play, f"play-{name}", stage=True)
        if view_too:
            self.s.take(self.view, f"view-{name}", stage=True)

    # ---- 進める ----

    def host_text(self, element_id: str) -> str:
        return self.host.locator(f"#{element_id}").inner_text().strip()

    def next_enabled(self) -> bool:
        return self.host.evaluate("() => !document.getElementById('next-btn').disabled")

    def press_next(self) -> None:
        if not _wait_until(self.next_enabled, REVEAL_SECONDS + 30, step=0.3):
            raise SystemExit(f"「次へ」が押せないまま止まりました（{self.host_text('next-sub')}）")
        time.sleep(max(0.0, self.last_press + MIN_ADVANCE_INTERVAL + 0.5 - time.monotonic()))
        self.host.locator("#next-btn").tap()
        self.last_press = time.monotonic()
        self.engine.advance()
        if not _wait_until(lambda: self.host_text("state") == STATE_TEXT[self.engine.state], 15):
            raise SystemExit(f"司会者画面が「{STATE_TEXT[self.engine.state]}」になりません")
        time.sleep(0.6)

    def tap_stage(self, x: float, y: float) -> None:
        box = self.play.locator("canvas").first.bounding_box()
        px, py = box["x"] + (x + 240) / 480 * box["width"], box["y"] + (180 - y) / 360 * box["height"]
        self.play.evaluate(TOUCH_JS, [px, py, 150])

    def shown(self, sprite: str, costume: str | None = None) -> list[dict[str, Any]]:
        js = """([name, costume]) => vm.runtime.targets.filter(t => !t.isStage && t.sprite.name === name && t.visible
          && (t.effects.ghost || 0) < 100 && (costume === null || t.getCostumes()[t.currentCostume].name === costume))
          .map(t => ({x: t.x, y: t.y}))"""
        return self.play.evaluate(js, [sprite, costume])

    def audience_answer(self) -> None:
        q = self.engine.current_question
        assert q is not None
        for page, value in zip(self.audience, (min(100, q.correct + 4), max(0, q.correct - 12)), strict=True):
            if _wait_until(lambda page=page: page.locator("#view-answer").is_visible(), 10):
                page.evaluate(
                    "(v) => { const s = document.getElementById('slider'); s.value = v; s.dispatchEvent(new Event('input', {bubbles: true})); }",
                    value,
                )
                page.locator("#lock-btn").tap()

    def wait_answer(self, value: int) -> None:
        if not _wait_until(lambda: str(value) in self.host_text("answer-now"), 15):
            raise SystemExit(f"回答 {value} が司会者画面に届きません")
        self.engine.submit_answer(value)

    # ---- 開く ----

    def open_all(self) -> None:
        h = self.host
        h.goto(f"{self.base}/host.html")
        h.locator("#token-input").wait_for(state="visible", timeout=15000)
        h.fill("#token-input", TOKEN)
        self.host_shot("host-token")
        h.press("#token-input", "Enter")
        _wait_until(self.next_enabled, 30)
        self.host_shot("host-idle")

        for page, path in ((self.play, "play"), (self.view, "view")):
            page.goto(f"{self.base}/stage/{path}/")
            page.wait_for_function("() => window.vm && vm.runtime && vm.runtime.targets.length > 5", timeout=60000)
            time.sleep(1.5)
        self.s.take(self.play, "play-launch")
        self.s.take(self.view, "view-launch", {"fullscreen": ".fullscreen-button"})
        size = self.play.viewport_size
        self.play.touchscreen.tap(size["width"] / 2, size["height"] / 2)
        self.view.mouse.click(VIEW["width"] / 2, VIEW["height"] / 2)
        time.sleep(4)
        self.stage_shot("idle", view_too=True)

        for i, (page, name) in enumerate(zip(self.audience, AUDIENCE, strict=True)):
            page.goto(f"{self.base}/suzuleague.html")
            page.locator("#join-name").wait_for(state="visible", timeout=15000)
            if i == 0:
                page.fill("#join-name", name)
                self.s.take(page, "aud-join", AUDIENCE_PARTS)
            else:
                page.fill("#join-name", name)
            page.press("#join-name", "Enter")
        time.sleep(1.5)
        self.s.take(self.audience[0], "aud-idle", AUDIENCE_PARTS)

    # ---- 1問 ----

    def question(self) -> None:
        """出題 → 回答受付 → 正解発表 → ラウンド結果。"""
        snap = self.engine.snapshot()
        team, rnd = snap.team_no, snap.round_no
        q = self.engine.current_question
        assert q is not None
        answer = stage_answer(team, rnd, q.correct)
        first = (team, rnd) == (1, 1)
        exhibition_first = team == 2 and rnd == 3

        # 出題（各チームの1問目は、バルーンが出そろうまで約9秒かかる）
        time.sleep(10 if rnd == 1 else 3)
        if first:
            self.host_shot("host-question")
            self.host.evaluate("() => document.getElementById('reveal-btn').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
            time.sleep(0.3)
            self.host_shot("host-reveal-peek")
            self.host.evaluate("() => document.getElementById('reveal-btn').dispatchEvent(new PointerEvent('pointerup', {bubbles: true}))")
            self.stage_shot("question", view_too=True)
            self.s.take(self.audience[0], "aud-question", AUDIENCE_PARTS)
        if exhibition_first:
            self.host_shot("host-exhibition")
            self.stage_shot("exhibition-question")

        # 回答受付
        self.press_next()
        time.sleep(2)
        if first:
            self.host_shot("host-answer-wait", scroll_to="#answer-card")
            _wait_until(lambda: bool(self.shown("キー")), 10)
            time.sleep(1)
            self.stage_shot("keypad", view_too=True)
        self.audience_answer()
        if first:
            self.s.take(self.audience[0], "aud-answered", AUDIENCE_PARTS)

        if rnd == 1:  # 各チームの1問目は iPad のテンキーで答える
            _wait_until(lambda: bool(self.shown("キー")), 10)
            for digit in str(answer):
                keys = self.shown("キー", digit)
                if keys:
                    self.tap_stage(keys[0]["x"], keys[0]["y"])
                    time.sleep(0.5)
            if first:
                time.sleep(0.5)
                self.stage_shot("typed")
            go = self.shown("決定")
            if go:
                self.tap_stage(go[0]["x"], go[0]["y"])
            self.wait_answer(answer)
            if first:
                time.sleep(1.5)
                self.stage_shot("sent", view_too=True)
                self.host_shot("host-answer-got", scroll_to="#answer-card")
        elif (team, rnd) == (1, 2):
            self.answer_on_player(answer)
        else:
            self.host.fill("#proxy-input", str(answer))
            if (team, rnd) == (1, 3):
                self.host_shot("host-proxy-typed", scroll_to="#answer-card")
            self.host.locator("#proxy-btn").tap()
            self.wait_answer(answer)
            if (team, rnd) == (1, 3):
                time.sleep(2)
                self.host_shot("host-proxy-done", scroll_to="#answer-card")
                self.stage_shot("proxied")
            if (team, rnd) == (3, 3):
                self.stop_and_resume()

        # 正解発表（ステージ画面のゲージが約13秒動いてから正解が出る）
        self.press_next()
        started = time.monotonic()
        if first:
            self.host_shot("host-reveal-hold")
            time.sleep(4)
            self.stage_shot("gauge", view_too=True)
            self.s.take(self.audience[0], "aud-wait-reveal", AUDIENCE_PARTS)
            time.sleep(max(0.0, started + 14.5 - time.monotonic()))
            self.stage_shot("correct", view_too=True)
            time.sleep(max(0.0, started + REVEAL_SECONDS + 1.5 - time.monotonic()))
            self.s.take(self.audience[0], "aud-reveal", AUDIENCE_PARTS)
        if (team, rnd) == (1, 2):
            time.sleep(max(0.0, started + REVEAL_SECONDS + 1.5 - time.monotonic()))
            self.s.take(self.player, "player-result", PLAYER_PARTS)

        # ラウンド結果
        self.press_next()
        time.sleep(3)
        if first:
            self.host_shot("host-round-result", scroll_to="#result-card")
            self.stage_shot("round-result")
        if (team, rnd) == (2, 2):
            self.host_shot("host-gameover", scroll_to="#result-card")
            time.sleep(2)
            self.stage_shot("gameover", view_too=True)

    def answer_on_player(self, answer: int) -> None:
        """チーム1の2問目は、予備の回答画面（スマホ）から4桁の合言葉で答える。"""
        self.host_shot("host-code")
        code = self.host_text("player-code")
        p = self.player
        p.goto(f"{self.base}/player.html")
        p.locator("#code-input").wait_for(state="visible", timeout=15000)
        self.s.take(p, "player-code-empty", PLAYER_PARTS)
        p.fill("#code-input", code)
        self.s.take(p, "player-code", PLAYER_PARTS)
        p.press("#code-input", "Enter")
        _wait_until(lambda: p.locator("#view-answer").is_visible(), 10)
        time.sleep(0.5)
        self.s.take(p, "player-answer", PLAYER_PARTS)
        for digit in str(answer):
            p.locator(f"#keys [data-key='{digit}']").tap()
        p.locator("#send-btn").evaluate("(el) => el.scrollIntoView({block: 'end'})")
        time.sleep(0.3)
        self.s.take(p, "player-typed", PLAYER_PARTS)
        p.locator("#send-btn").tap()
        self.wait_answer(answer)
        time.sleep(1.5)
        p.locator("#send-status").evaluate("(el) => el.scrollIntoView({block: 'end'})")
        time.sleep(0.3)
        self.s.take(p, "player-sent", PLAYER_PARTS)

    def stop_and_resume(self) -> None:
        self.dashboard.stop()
        _wait_until(lambda: "応答なし" in self.host_text("conn-text"), 20)
        time.sleep(1)
        self.host_shot("host-no-response")
        self.dashboard.start(resume=True)
        if not _wait_until(lambda: "接続中" in self.host_text("conn-text") and self.next_enabled(), 60):
            raise SystemExit("「続きから」で再開できません")

    # ---- 通し ----

    def run(self) -> None:
        self.open_all()
        self.press_next()  # チーム紹介
        time.sleep(4)
        self.host_shot("host-intro")
        self.stage_shot("intro", view_too=True)
        self.s.take(self.audience[0], "aud-intro", AUDIENCE_PARTS)
        while self.engine.state is not State.FINISHED:
            state = self.engine.state
            if state is State.TEAM_INTRO or (state is State.ROUND_RESULT and self.engine.snapshot().round_no < 5):
                self.press_next()  # 出題
                self.question()
            elif state is State.ROUND_RESULT:
                self.press_next()  # チーム結果
                self.team_result()
            elif state is State.TEAM_RESULT:
                self.press_next()  # 次のチーム・全体結果
                if self.engine.state is State.TEAM_INTRO:
                    time.sleep(1)
            else:
                raise SystemExit(f"想定していない進行: {state}")
        self.final()

    def team_result(self) -> None:
        team = self.engine.snapshot().team_no
        time.sleep(9)  # ステージ画面のクリア・失敗の演出が終わるまで待つ
        if team == 1:
            self.host_shot("host-team-clear")
            self.stage_shot("team-clear", view_too=True)
        if team == 2:
            self.host_shot("host-team-fail")
            self.stage_shot("team-fail")
        if team == 3:  # 困ったときの欄を開いて、送り直しを押す
            self.host.locator("#trouble-card > summary").tap()
            time.sleep(0.5)
            self.host_shot("host-trouble", scroll_to="#trouble-card")
            self.host.locator("#resync-btn").tap()
            time.sleep(2.5)
            self.host_shot("host-resync-done", scroll_to="#trouble-card")
            self.host.locator("#trouble-card > summary").tap()

    def final(self) -> None:
        time.sleep(6)
        self.host_shot("host-final")
        self.stage_shot("final", view_too=True)
        _wait_until(lambda: self.audience[0].locator("#view-final").is_visible(), 20)
        time.sleep(1)
        self.s.take(self.audience[0], "aud-final", AUDIENCE_PARTS)
        self.host.locator("#teams-card > summary").tap()
        time.sleep(0.5)
        self.host_shot("host-teams", scroll_to="#teams-card")
        self.host.locator("#teams-card > summary").tap()
        self.host.locator("#ranking-card > summary").tap()
        _wait_until(lambda: AUDIENCE[0] in self.host_text("ranking"), 10)
        self.host_shot("host-ranking", scroll_to="#ranking-reset")
        # 合言葉を間違えたとき
        self.host.evaluate("() => localStorage.setItem('suzuleague-host-token', 'wrong')")
        self.host.reload()
        self.host.locator("#token-input").wait_for(state="visible", timeout=15000)
        time.sleep(0.5)
        self.host_shot("host-token-wrong")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使い方マニュアルに載せる画面を撮る")
    parser.add_argument("out", type=Path, help="画像の書き出し先（プロジェクトの外の一時フォルダにする）")
    parser.add_argument("--server-dir", type=Path, default=DEFAULT_SERVER_DIR, help="cloud-server のクローン先")
    parser.add_argument("--teams", type=Path, default=REPO / "teams.json", help="チーム構成（本番と同じ teams.json）")
    parser.add_argument("--headed", action="store_true", help="ブラウザを見えるように開く")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    port = _free_port()
    os.environ["HOST_TOKEN"] = TOKEN
    with tempfile.TemporaryDirectory() as tmp, cloud_server(args.server_dir, port), sync_playwright() as pw:
        dashboard = SampleDashboard(port, args.teams, Path(tmp))
        dashboard.start()
        browser = pw.chromium.launch(headless=not args.headed, args=BROWSER_ARGS)
        try:
            Capture(pw, browser, f"http://localhost:{port}", Shots(args.out), dashboard, args.teams).run()
        finally:
            browser.close()
            dashboard.stop()
    print(f"\n書き出し先: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

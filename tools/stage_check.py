"""作り直したステージ画面を、手元で通しに動かして確かめる（#58）。

Scratch担当がプロジェクトを直すたびに、ステージ画面を作り直す（`uv run python -m suzuleague.stage`）。
そのあとで、書き出した画面が本番の進行どおりに動くかをこれで確かめる。

- 隣の cloud-server（`../cloud-server`）を手元で起動し、書き出した `public/stage/play/`（出演者用）と
  `public/stage/view/`（大画面用）を自動操作のブラウザで開く
- 裏方PCと同じ進行（GameEngine）を同じサーバにつないで、3チームぶん進める
  - チーム1: 5問とも1ずれ → クリア
  - チーム2: 2問目で大きく外してゲームオーバー → 残りはエキシビション
  - チーム3: 5問とも1ずれ → チーム1と同点で優勝を分け合う
- 段階ごとに、Scratch の中の状態（問題文・問題番号・チームの札・残りバルーン・予想・正解・文字）を、
  裏方PCの状態と照らし合わせる
- 出題・回答受付・正解発表・ラウンド結果・チーム結果・全体結果で開き直して、
  開き直さずに見ていた画面と同じ部品が出るかを比べる
- 出演者用はテンキーをクリックして答え、回答が裏方PCに届くかを見る

    # 初回だけ: ブラウザを入れる
    uv run --with playwright python -m playwright install chromium

    uv run --with playwright python tools/stage_check.py
    uv run --with playwright python tools/stage_check.py --quick      # 1チームだけ（約3分）
    uv run --with playwright python tools/stage_check.py --headed     # ブラウザを見ながら

通しで10分ほどかかる（正解発表の演出を毎回待つため）。音は確かめられないので、実機で聞く。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from suzuleague.cloud import CloudBridge
from suzuleague.controller import GameController
from suzuleague.engine import GameEngine, GameError, State
from suzuleague.models import Team
from suzuleague.protocol import REVEAL_SECONDS
from suzuleague.publish import DEFAULT_SERVER_DIR
from suzuleague.stage import SCRATCH_PROJECT_ID

INITIAL_BALLOONS = 30  # 少なめにして、1問でゲームオーバーにできるようにする

# 画面の中の状態を取り出す。見えている部品（スプライトとクローン）と、ステージの変数
SNAPSHOT_JS = """() => {
  const rt = vm.runtime;
  const parts = [];
  for (const t of rt.targets) {
    if (t.isStage) continue;
    const local = {};
    for (const v of Object.values(t.variables)) if (v.type === "") local[v.name] = v.value;
    parts.push({
      name: t.sprite.name,
      costume: t.getCostumes()[t.currentCostume].name,
      shown: t.visible && (t.effects.ghost || 0) < 100,
      x: Math.round(t.x), y: Math.round(t.y),
      role: local["役割"] !== undefined ? String(local["役割"]) : (local["役"] !== undefined ? String(local["役"]) : ""),
      local,
    });
  }
  const stage = rt.getTargetForStage();
  const vars = {};
  for (const v of Object.values(stage.variables)) if (v.type === "") vars[v.name] = v.value;
  return { parts, vars, backdrop: stage.getCostumes()[stage.currentCostume].name };
}"""

# 位置まで比べる部品（ほかは動き続けるので、出ているかとコスチュームだけ比べる）
PLACED = ("問題", "問題数", "予想", "正解")
# 開き直しで比べない部品。風船は1個ずつふわふわ動き、出るのにも時間差がある。残りの数字は、数が変わるたびに
# クローンを作り直す（どちらも数は 風船の数 で見る）
UNSTABLE_ROLES = {("UI", "風船")}
UNSTABLE_SPRITES = {"数字"}
REOPEN_SETTLE = 4.0  # 開き直してから、画面を組み立て終えるまで待つ秒数


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    passed: int = 0

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  ✓ {label}")
        else:
            self.failures.append(f"{label}{'：' + detail if detail else ''}")
            print(f"  ✗ {label}{'：' + detail if detail else ''}")
        return ok


# ---- 手元のサーバ ----------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def cloud_server(server_dir: Path, port: int) -> Iterator[None]:
    """cloud-server を手元で起動する（書き出した public/stage/ もここから配る）。"""
    if not (server_dir / "node_modules").is_dir():
        print("  cloud-server の依存を入れています（最初の1回だけ）…")
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=server_dir, check=True)
    proc = subprocess.Popen(
        ["node", "src/index.js"],
        cwd=server_dir,
        env=os.environ | {"PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                urllib.request.urlopen(f"http://localhost:{port}/stage/play/version.json", timeout=1)
                break
            except OSError:
                if time.monotonic() > deadline or proc.poll() is not None:
                    raise RuntimeError("cloud-server が起動しません（public/stage/ に書き出してありますか）") from None
                time.sleep(0.3)
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# ---- 画面 ------------------------------------------------------------


# 画面の大きさとブラウザの描き方。GPU のない環境では、大きな画面だと1秒に5コマほどしか進まず、
# コマ数で長さが決まる演出（明るさを100回変える、など）が実機の何倍もかかる。
# 実機と同じ大きさ（480x360）にして SwiftShader で描くと、実機と同じ1秒30コマで動く
VIEWPORT = {"width": 480, "height": 400}
BROWSER_ARGS = [
    "--autoplay-policy=no-user-gesture-required",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
]


class StagePage:
    """自動操作のブラウザで開いたステージ画面1つ。"""

    def __init__(self, browser: Any, url: str, label: str, seq: Callable[[], int]) -> None:
        self.url, self.label, self.seq = url, label, seq
        self.page = browser.new_page(viewport=VIEWPORT)
        self.open()

    def open(self) -> None:
        self.page.goto(self.url)
        self.page.wait_for_function("() => window.vm && vm.runtime && vm.runtime.targets.length > 5", timeout=60000)
        # 裏方PCの状態が届いてからクリックする（届く前に始めると、既定の値で組み立てたあとでもう一度組み立てる）
        seq = self.seq()
        self.page.wait_for_function(
            """(seq) => Object.values(vm.runtime.getTargetForStage().variables)
                 .some(v => v.name === "☁ P2S_SEQ" && Number(v.value) === seq)""",
            arg=seq,
            timeout=10000,
        )
        self.page.mouse.click(VIEWPORT["width"] / 2, VIEWPORT["height"] / 2)  # 最初の1回のクリックで始まる（旗）
        self.page.wait_for_function("() => vm.runtime.threads.length > 0", timeout=10000)
        time.sleep(1.5)  # 届いている状態から画面を組み立て終わるまで

    def snapshot(self) -> dict[str, Any]:
        return self.page.evaluate(SNAPSHOT_JS)

    def shown(self, snap: dict[str, Any], name: str, costume: str | None = None) -> list[dict[str, Any]]:
        return [p for p in snap["parts"] if p["name"] == name and p["shown"] and (costume is None or p["costume"] == costume)]

    def click_stage(self, x: float, y: float) -> None:
        """ステージの座標（中心が 0,0、上が +）をクリックする。"""
        box = self.page.locator("canvas").first.bounding_box()
        self.page.mouse.move(box["x"] + (x + 240) / 480 * box["width"], box["y"] + (180 - y) / 360 * box["height"])
        # Scratch はマウスが押されているかを1コマごとに見ているので、少し押したままにする
        self.page.mouse.down()
        time.sleep(0.15)
        self.page.mouse.up()


def summary(snap: dict[str, Any], only: set[str] | None = None) -> Counter[tuple[Any, ...]]:
    """開き直しで比べる形: 見えている部品の（名前・コスチューム・役割）と、決まった位置にいるものは位置も。

    only を渡すと、そのスプライトだけを比べる（全体結果は、ほかの部品を背景で覆っているだけなので）。
    """
    out: Counter[tuple[Any, ...]] = Counter()
    for p in snap["parts"]:
        if not p["shown"] or (p["name"], p["role"]) in UNSTABLE_ROLES or p["name"] in UNSTABLE_SPRITES:
            continue
        if only and p["name"] not in only:
            continue
        # 全体結果の元のスプライトは、クローンを作るたびに 役 を変える（背景のまま見えている）
        key: tuple[Any, ...] = (p["name"], p["costume"], "" if p["name"] == "結果発表" and p["costume"] == "背景" else p["role"])
        if p["name"] in PLACED:
            key += (p["x"], p["y"])
        out[key] += 1
    return out


def _wait_until(condition: Callable[[], bool], timeout: float, step: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(step)
    return condition()


# ---- 通し ------------------------------------------------------------


class Run:
    def __init__(self, args: argparse.Namespace, browser: Any, port: int) -> None:
        self.report = Report()
        self.reopen = not args.no_reopen
        teams = [Team(number=n, name=f"チーム{n}") for n in range(1, 2 if args.quick else 4)]
        self.engine = GameEngine(teams=teams, initial_balloons=INITIAL_BALLOONS)
        self.received: list[int] = []
        self.bridge = CloudBridge(args.room_id, cloud_host=f"ws://localhost:{port}", on_answer=self._on_answer)
        self.controller = GameController(self.engine, self.bridge)
        self.bridge.connect()
        base = f"http://localhost:{port}/stage"
        self.play = StagePage(browser, f"{base}/play/", "出演者用", self.seq)
        self.view = StagePage(browser, f"{base}/view/", "大画面用", self.seq)
        self.pages = (self.play, self.view)

    def seq(self) -> int:
        """最後に送った P2S_SEQ（まだ何も送っていなければ 0）。"""
        return self.bridge._seq

    def _on_answer(self, percent: int) -> None:
        self.received.append(percent)
        with contextlib.suppress(GameError):
            self.controller.answer(percent)

    def advance(self, settle: float = 2.0) -> State:
        state = self.controller.advance()
        time.sleep(settle)
        return state

    # -- 照合 --

    def check_common(self, title: str, *, revealed: bool = False) -> None:
        """どの段階でも合っているべきもの（問題文・問題番号・チームの札・残りバルーン）。"""
        snap_engine = self.engine.snapshot()
        # ゲームオーバー・クリアになった問題の正解発表では、元の演出が問題文と問題番号を隠す（ラウンド結果でまた出る）
        hidden = self.engine.state is State.REVEAL and ends_team(self.engine.last_result)
        for page in self.pages:
            snap = page.snapshot()
            where = f"{title}・{page.label}"
            team, round_no = snap_engine.team_no, snap_engine.round_no
            if self.engine.state in (State.QUESTION, State.ANSWERING, State.EXHIBITION_ANSWERING, State.REVEAL, State.ROUND_RESULT):
                if hidden:
                    question = page.shown(snap, "問題") + page.shown(snap, "問題数")
                    self.report.check(not question, f"{where}: 問題文と問題番号が隠れる（ゲームオーバー・クリアの演出）")
                    continue
                question = page.shown(snap, "問題", f"{team}-{round_no}")
                self.report.check(bool(question), f"{where}: 問題文が {team}-{round_no}", str([p["costume"] for p in page.shown(snap, "問題")]))
                number = page.shown(snap, "問題数", str(round_no))
                self.report.check(bool(number), f"{where}: 問題番号が {round_no}", str([p["costume"] for p in page.shown(snap, "問題数")]))
                tag = [p for p in page.shown(snap, "UI") if p["role"] == "7"]
                self.report.check(
                    [p["costume"] for p in tag] == [f"チーム{team}"], f"{where}: チームの札が チーム{team}", str([p["costume"] for p in tag])
                )
            if self.engine.state is not State.FINISHED:
                balloons = snap["vars"].get("風船の数")
                expected = snap_engine.balloons
                if revealed or self.engine.state not in (State.REVEAL,):
                    self.report.check(
                        _num(balloons) == expected, f"{where}: 残りバルーンが {expected}", f"画面は {balloons}"
                    )

    def check_answer_shown(self, title: str, answer: int) -> None:
        for page in self.pages:
            ok = _wait_until(lambda page=page: _num(page.snapshot()["vars"].get("予想%")) == answer, 3)
            self.report.check(ok, f"{title}・{page.label}: 予想が {answer}", str(page.snapshot()["vars"].get("予想%")))

    def compare_after_reopen(
        self,
        title: str,
        extra: Callable[[StagePage, dict[str, Any]], None] | None = None,
        only: set[str] | None = None,
    ) -> None:
        """開き直す前と後で、見えている部品が同じか（only を渡すと、そのスプライトだけ比べる）。"""
        if not self.reopen:
            return
        if self.engine.state is State.FINISHED:
            only = {"結果発表"}
        for page in self.pages:
            before = page.snapshot()
            page.open()
            time.sleep(REOPEN_SETTLE)
            after = page.snapshot()
            missing = summary(before, only) - summary(after, only)
            added = summary(after, only) - summary(before, only)
            self.report.check(
                not missing and not added,
                f"{title}・{page.label}: 開き直しても同じ画面",
                f"消えた {dict(missing)} / 増えた {dict(added)}",
            )
            revealed = self.engine.state in (State.REVEAL, State.ROUND_RESULT, State.TEAM_RESULT)
            for name in ("風船の数", "予想%", "正解%") if revealed else ("風船の数",):
                if self.engine.state is not State.FINISHED:
                    self.report.check(
                        _num(before["vars"].get(name)) == _num(after["vars"].get(name)),
                        f"{title}・{page.label}: 開き直しても {name} が同じ",
                        f"{before['vars'].get(name)} → {after['vars'].get(name)}",
                    )
            if extra:
                extra(page, after)

    # -- 1チームぶん --

    def play_team(self, plan: Callable[[int, int], int]) -> None:
        """plan(問題番号, 正解) → 答え。"""
        team = self.engine.current_team
        assert team is not None
        title = f"チーム{team.number}"
        print(f"\n== {title} ==")
        self.check_common(f"{title} チーム紹介")
        for round_no in range(1, 6):
            q_title = f"{title} {round_no}問目"
            self.controller.advance()  # 出題
            started = time.monotonic()
            if round_no == 1:
                # 1問目も、登場の効果音を待たずに問題文が出る（#59。元は約8秒かかった）
                shown = _wait_until(lambda: bool(self.view.shown(self.view.snapshot(), "問題", f"{team.number}-1")), 6)
                self.report.check(shown and time.monotonic() - started < 4, f"{q_title}: 問題文がすぐ出る", f"{time.monotonic() - started:.1f}秒")
            time.sleep(2.5)
            self.check_common(f"{q_title} 出題")
            if round_no == 1:
                self.compare_after_reopen(f"{q_title} 出題")
            state = self.advance(1.5)  # 回答受付
            exhibition = state is State.EXHIBITION_ANSWERING
            self.check_common(f"{q_title} 回答受付")
            if exhibition and round_no == 3:
                self.compare_after_reopen(
                    f"{q_title} エキシビションの回答受付",
                    lambda page, snap: self.report.check(
                        _num(snap["vars"].get("再生中")) == 1, f"{page.label}: 開き直しても音楽が戻る", str(snap["vars"].get("再生中"))
                    ),
                )
            question = self.engine.current_question
            assert question is not None
            answer = plan(round_no, question.correct)
            if round_no == 1:
                self.answer_with_keypad(q_title, answer)
            else:
                self.controller.answer(answer)
            self.check_answer_shown(f"{q_title} 回答", answer)
            if round_no == 2:
                self.compare_after_reopen(f"{q_title} 回答受付（回答あり）")
            self.advance(REVEAL_SECONDS)  # 正解発表（ゲージが止まるまで待つ）
            result = self.engine.last_result
            assert result is not None
            self.wait_reveal_done(result)
            self.check_common(f"{q_title} 正解発表", revealed=True)
            self.check_end_texts(f"{q_title} 正解発表", result)
            if round_no in (2, 5) or ends_team(result):
                # ゲームオーバー・クリアの問題は、開き直すとラウンド結果と同じ画面（問題文も出る）になるので、文字だけ比べる
                self.compare_after_reopen(
                    f"{q_title} 正解発表",
                    lambda page, snap, result=result: self.check_end_texts_on(page, snap, result),
                    only={"GameOver", "UI_結果演出"} if ends_team(result) else None,
                )
            self.advance(2.0)  # ラウンド結果
            self.check_common(f"{q_title} ラウンド結果", revealed=True)
            if round_no == 5:
                self.compare_after_reopen(f"{q_title} ラウンド結果", lambda page, snap, result=result: self.check_end_texts_on(page, snap, result))
        self.advance(2.0)  # チーム結果
        self.compare_after_reopen(f"{title} チーム結果")

    def wait_reveal_done(self, result: Any) -> None:
        """風船が割れ終わり、GAME OVER・CLEARED の文字が出終わるまで待つ。"""
        for page in self.pages:
            _wait_until(lambda page=page: _num(page.snapshot()["vars"].get("風船の数")) == result.balloons_after, 20, 0.5)
        if result.balloons_after == 0 or result.round_no == 5:
            time.sleep(5.0)  # 文字は数秒待ってから、1文字ずつ（大きくなりながら）出る
        else:
            time.sleep(1.5)  # 風船を割る鳥が出ていく

    def answer_with_keypad(self, title: str, answer: int) -> None:
        """出演者用のテンキーをクリックして答え、裏方PCに届くかを見る。"""
        page = self.play
        self.received.clear()
        ok = _wait_until(lambda: any(p["name"] == "キー" for p in page.shown(page.snapshot(), "キー")), 8)
        self.report.check(ok, f"{title}: テンキーが出る")
        if not ok:
            self.controller.answer(answer)
            return
        for digit in str(answer):
            keys = page.shown(page.snapshot(), "キー", digit)  # コスチュームの名前が数字
            if not keys:
                self.report.check(False, f"{title}: テンキーの {digit}", "見つからない")
                self.controller.answer(answer)
                return
            page.click_stage(keys[0]["x"], keys[0]["y"])
            time.sleep(0.4)
        go = page.shown(page.snapshot(), "決定")
        if go:
            page.click_stage(go[0]["x"], go[0]["y"])
        self.report.check(
            _wait_until(lambda: self.engine.pending_answer == answer, 5),
            f"{title}: テンキーの回答 {answer} が裏方PCに届く",
            f"届いたのは {self.received}",
        )

    def check_end_texts(self, title: str, result: Any) -> None:
        for page in self.pages:
            self.check_end_texts_on(page, page.snapshot(), result, title)

    def check_end_texts_on(self, page: StagePage, snap: dict[str, Any], result: Any, title: str = "開き直したあと") -> None:
        """その問題でゲームオーバー・クリアになったら、その文字が出ている（なっていなければ出ていない）。"""
        game_over = result.balloons_after == 0 and not result.exhibition
        letters = [p for p in page.shown(snap, "GameOver")]
        self.report.check(
            (len(letters) >= 8) == game_over,
            f"{title}・{page.label}: GAME OVER の文字が{'出る' if game_over else '出ない'}",
            f"{len(letters)}文字",
        )
        cleared = result.round_no == 5 and result.balloons_after > 0 and self.engine.state in (State.REVEAL, State.ROUND_RESULT)
        text = [p for p in page.shown(snap, "UI_結果演出", "Perfect_cleared") if p["role"] == "1"]
        self.report.check(bool(text) == cleared, f"{title}・{page.label}: CLEARED の文字が{'出る' if cleared else '出ない'}")

    def check_final(self) -> None:
        print("\n== 全体結果 ==")
        self.advance(1.0)
        assert self.engine.state is State.FINISHED
        winners = [t.number for t in self.engine.winners()]
        time.sleep(len(self.engine.teams) * 0.5 + 3)
        for page in self.pages:
            snap = page.snapshot()
            cards = sorted(p["costume"] for p in page.shown(snap, "結果発表") if p["role"] == "カード")
            self.report.check(
                cards == [f"カード{t.number}" for t in self.engine.teams], f"全体結果・{page.label}: チームごとのカード", str(cards)
            )
            crowns = page.shown(snap, "結果発表", "優勝")
            self.report.check(len(crowns) == len(winners), f"全体結果・{page.label}: 優勝の札が {len(winners)} つ", f"{len(crowns)} つ")
            failed = sum(1 for t in self.engine.teams if t.is_failed)
            marks = page.shown(snap, "結果発表", "ゲームオーバー")
            self.report.check(len(marks) == failed, f"全体結果・{page.label}: GAME OVER の札が {failed} つ", f"{len(marks)} つ")
        self.compare_after_reopen("全体結果")

    def run(self) -> None:
        print("== 待機 ==")
        self.advance()  # チーム1の紹介
        teams = len(self.engine.teams)
        # ぴったりにはしない（ぴったり賞の演出は、終わるときに CLEARED の文字も消してしまう。元の作りのまま）
        def near(correct: int) -> int:
            return correct + 1 if correct < 100 else correct - 1

        plans: list[Callable[[int, int], int]] = [
            lambda round_no, correct: near(correct),  # 毎回1ずれ → クリア
            lambda round_no, correct: (0 if correct >= 50 else 100) if round_no == 2 else near(correct),  # 2問目でゲームオーバー
            lambda round_no, correct: near(correct),  # 毎回1ずれ → チーム1と同点
        ]
        for i in range(teams):
            if i:
                self.advance()  # 次のチームの紹介
            self.play_team(plans[i])
        self.check_final()


def ends_team(result: Any) -> bool:
    """その問題でゲームオーバーになったか、最後の問題をクリアしたか（元の演出が問題文を隠す）。"""
    if result is None or result.exhibition:
        return False
    return result.balloons_after == 0 or (result.round_no == 5 and result.balloons_after > 0)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="作り直したステージ画面を、手元で通しに動かして確かめる")
    parser.add_argument("--server-dir", type=Path, default=DEFAULT_SERVER_DIR, help="cloud-server のクローン先")
    parser.add_argument("--room-id", default=SCRATCH_PROJECT_ID, help="書き出したときのルームID")
    parser.add_argument("--quick", action="store_true", help="1チームだけ通す")
    parser.add_argument("--no-reopen", action="store_true", help="開き直しの確かめを省く")
    parser.add_argument("--headed", action="store_true", help="ブラウザを見えるように開く")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    port = _free_port()
    with cloud_server(args.server_dir, port), sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed, args=BROWSER_ARGS)
        run = Run(args, browser, port)
        try:
            run.run()
        finally:
            run.bridge.disconnect()
            browser.close()
    report = run.report
    print(f"\n{report.passed} 件 OK、{len(report.failures)} 件 NG")
    for failure in report.failures:
        print(f"  ✗ {failure}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())

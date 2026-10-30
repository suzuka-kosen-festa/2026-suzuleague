"""Scratch のステージ画面を本番用に作り直し、cloud-server から配れる形にする。

元は Scratch担当のプロジェクト（https://scratch.mit.edu/projects/1364239598/）。
絵・音・演出はそのまま使い、そこから2つの画面を作る。

- view（大画面用）: 入力なし。テンキーを外し、出演者の回答は ☁ P2S_ANSWER から出す
- play（出演者用）: 入力あり。テンキーで答える

全体結果の画面だけは元のものを使わず、stage_result.py で作ったものに置き換える。

どちらも、裏方PCとやり取りする「通信」スプライトのスクリプトだけを、ここで組み立てた
ものに入れ替える。元のスクリプトは「チーム紹介から順に見てきた」前提で画面を積み上げるので、
途中で開き直すと崩れた（#52）。入れ替え後は次のように動く。

- 1つ前の段階から順に進んだときだけ、元の演出（メッセージ）をそのまま流す
- それ以外（開き直した・送り直された・段階を飛ばした）は、画面を消して今の状態から作り直す

演出そのもの（各スプライトがメッセージを受けて動く部分）には手を入れない。
頼っているのは REQUIRED_MESSAGES のメッセージ名と REQUIRED_VARIABLES の変数だけなので、
Scratch担当が絵や演出を直しても、作り直せばそのまま取り込める。

    # 作って cloud-server の public/stage/ に書き出す（隣に cloud-server がある前提）
    uv run python -m suzuleague.stage

    # 本番で配信中のものが、今の Scratch プロジェクトから作ったものと同じか確かめる
    uv run python -m suzuleague.stage --check

    # 書き出してある画面に、リハーサル用の問題文だけを足す（Scratch の新しい版を取り込まずに済ませる）
    uv run python -m suzuleague.stage --add-rehearsal

HTML への書き出しは TurboWarp Packager（tools/stage-packager）で行う。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from . import stage_result
from .publish import DEFAULT_SERVER_DIR, PRODUCTION_URL
from .questions import REHEARSAL_IDS
from .sb3 import (
    Block,
    Menu,
    ProjectError,
    Scripts,
    Var,
    absolute,
    add_to,
    blocks_of,
    both,
    broadcast,
    call,
    change_var,
    create_clone,
    delete_all,
    divide,
    either,
    eq,
    find_target,
    forever,
    gt,
    hide_list,
    join,
    length,
    letter,
    lt,
    minus,
    no,
    of_sprite,
    on_flag,
    on_message,
    plus,
    receivers,
    rename_stage_variables,
    repeat,
    repeat_until,
    script_blocks,
    set_var,
    stage_of,
    stop_other_scripts,
    wait,
    when,
    when_else,
)
from .teams import load_teams

SCRATCH_PROJECT_ID = "1364239598"
VARIANTS = {"view": "大画面用（入力なし）", "play": "出演者用（入力あり）"}
PACKAGER_DIR = Path(__file__).resolve().parents[2] / "tools" / "stage-packager"
DEFAULT_TEAMS = Path(__file__).resolve().parents[2] / "teams.json"  # アプリの「本番」が読むのと同じファイル

# 「通信」が各スプライトへ送るメッセージ。受け手がなくなっていたら元の作りが変わっている。
# 1_チーム紹介 も送るが、10/18 の版から受け手がない（チーム名の札は ☁ P2S_TEAM を見て出し続ける）ので、ここには入れない
REQUIRED_MESSAGES = (
    "初期化",
    "演出_風船登場開始",
    "進行_次の問題へ",
    "4_正解発表",
    "5_ラウンド結果表示",
    "6_チーム結果",
    "途中復帰_背景同期",
    "途中復帰_出題UI同期",
    "途中復帰_入力監視開始",
    "演出_バルーン破裂完了",
)
REQUIRED_VARIABLES = ("回答中", "風船の数", "予想%", "正解%", "誤差", "エキシビション", "途中復帰", "復帰描画中")
REQUIRED_LISTS = ("★入力した文字", "得点表")
HIDE_GUESS = "予想%消して"  # 1 のあいだ予想の数字を隠す（ゲームオーバー・クリアの正解発表。10/18 の版から）
INPUT_SPRITES = ("キー", "決定", "delete")  # テンキー・GO・Delete

# 開いた直後（裏方PCからまだ何も届いていないとき）の値。元のプロジェクトには保存したときの
# 値が残っていて、裏方PCより先に開くとそれを今の状態と取り違えていた
CLOUD_DEFAULTS = {
    "☁ P2S_SEQ": "0",
    "☁ P2S_STATE": "0",
    "☁ P2S_TEAM": "0",
    "☁ P2S_ROUND": "0",
    "☁ P2S_QID": "0",
    "☁ P2S_CORRECT": "-1",
    "☁ P2S_BALLOONS": "0",
    "☁ P2S_ANSWER": "-1",
    "☁ P2S_SCORES": "1",
    "☁ P2S_FAILED_ROUND": "0",
    "☁ S2P_ANSWER": "0",
    "☁ S2P_SEQ": "0",
}

STATE, TEAM, ROUND, QID = "☁ P2S_STATE", "☁ P2S_TEAM", "☁ P2S_ROUND", "☁ P2S_QID"
CORRECT, BALLOONS, ANSWER, SCORES = "☁ P2S_CORRECT", "☁ P2S_BALLOONS", "☁ P2S_ANSWER", "☁ P2S_SCORES"
FAILED_ROUND = "☁ P2S_FAILED_ROUND"


class StageError(ProjectError):
    """元のプロジェクトや Packager の出力が想定と違っていて、作り直せない。"""


def _optional_target(project: dict[str, Any], name: str) -> dict[str, Any] | None:
    """スプライトがあれば返す（なければ None。なくても作り直しは止めない直しに使う）。"""
    try:
        return find_target(project, name)
    except ProjectError:
        return None


def _has_variable(target: dict[str, Any] | None, name: str) -> bool:
    return target is not None and any(v[0] == name for v in target["variables"].values())


# ---- 元のプロジェクトを取ってくる ------------------------------------


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "suzuleague-stage"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read().decode("utf-8"))


def fetch_project(project_id: str = SCRATCH_PROJECT_ID) -> tuple[dict[str, Any], str]:
    """公開中の Scratch プロジェクトの中身と、最後に更新された日時を返す。"""
    meta = _get_json(f"https://api.scratch.mit.edu/projects/{project_id}")
    project = _get_json(f"https://projects.scratch.mit.edu/{project_id}?token={meta['project_token']}")
    return project, str(meta["history"]["modified"])


def check_source(project: dict[str, Any]) -> None:
    """頼っているメッセージ・変数・スプライトがそろっているか確かめる。"""
    problems = []
    stage = stage_of(project)
    names = {t["name"] for t in project["targets"]}
    for sprite in ("通信", "問題", *INPUT_SPRITES):
        if sprite not in names:
            problems.append(f"スプライト「{sprite}」がない")
    for message in REQUIRED_MESSAGES:
        if not [t for t, _ in receivers(project, message) if t["name"] != "通信"]:
            problems.append(f"メッセージ「{message}」を受け取るスクリプトがない")
    variables = {v[0] for v in stage["variables"].values()}
    for name in (*REQUIRED_VARIABLES, STATE, TEAM, ROUND, QID, CORRECT, BALLOONS, "☁ P2S_SEQ"):
        if name not in variables:
            problems.append(f"変数「{name}」がない")
    lists = {v[0] for v in stage["lists"].values()}
    for name in REQUIRED_LISTS:
        if name not in lists:
            problems.append(f"リスト「{name}」がない")
    if problems:
        raise StageError("元の Scratch プロジェクトの作りが変わっています: " + " / ".join(problems))


# ---- 作り直し --------------------------------------------------------


def transform(
    project: dict[str, Any], variant: str, team_names: dict[int, str] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """元のプロジェクトから view / play の画面を作る。当てられなかった直しの説明も返す。

    team_names（チーム番号 → チーム名）を渡すと、札と全体結果のカードに番号の代わりに名前を出す。
    """
    if variant not in VARIANTS:
        raise ValueError(f"画面の種類は {' / '.join(VARIANTS)} のどちらか: {variant!r}")
    check_source(project)
    project = copy.deepcopy(project)
    notes: list[str] = []

    _set_cloud_variables(project)
    _replace_communication(project, variant)
    _add_rebuild_helpers(project, notes)
    _keep_keypad_on_first_question(project, notes)
    _show_first_question_with_start_sound(project, notes)
    _add_rehearsal_questions(project)
    stage_result.add_result_screen(project, team_names)
    _put_team_names(project, team_names or {}, notes)
    if variant == "view":
        for name in INPUT_SPRITES:
            target = find_target(project, name)
            target["blocks"] = {}
            target["comments"] = {}
            target["visible"] = False
    _drop_unused_cloud_variables(project, variant)
    return project, notes


def _put_team_names(project: dict[str, Any], team_names: dict[int, str], notes: list[str]) -> None:
    """チーム紹介と出題中に出る札（スプライト UI のコスチューム「チームN」）を、チーム名を書いた絵にする（#59）。

    クラウド変数は数値しか送れないので、名前は書き出すときに絵として埋め込む。
    """
    if not team_names:
        return
    ui = _optional_target(project, "UI")
    costumes = {c["name"]: c for c in ui["costumes"]} if ui else {}
    for team, name in sorted(team_names.items()):
        costume = costumes.get(f"チーム{team}")
        if costume is None:
            notes.append(f"チーム{team}の札に名前を出していません（スプライト UI にコスチューム「チーム{team}」がない）")
            continue
        tag = stage_result.team_tag(team, name)
        costume |= {
            "assetId": tag.md5ext.removesuffix(".svg"),
            "md5ext": tag.md5ext,
            "dataFormat": "svg",
            "bitmapResolution": 1,
            "rotationCenterX": tag.center[0],
            "rotationCenterY": tag.center[1],
        }


def _add_rehearsal_questions(project: dict[str, Any]) -> None:
    """リハーサルの ID（questions.REHEARSAL_IDS）が届いたら、問題文の絵をリハーサル用にする。

    リハーサルでも本番と同じ画面を開くので、本番の問題文を出演者や周りに見せないために要る。
    スプライト「問題」で絵を切り替えるブロックのすぐ下で、リハーサルなら「R1-1」などに切り替え直す。
    同じスクリプトの中で続けて切り替えるので、本番の問題文が画面に出るすきはない。
    """
    target = find_target(project, "問題")
    s = Scripts(project, "問題")
    switches = [i for i, b in blocks_of(target).items() if b["opcode"] == "looks_switchcostumeto"]
    for costume in stage_result.rehearsal_questions():
        target["costumes"].append(
            {
                "name": costume.name,
                "bitmapResolution": 1,
                "dataFormat": "svg",
                "assetId": costume.md5ext.removesuffix(".svg"),
                "md5ext": costume.md5ext,
                "rotationCenterX": costume.center[0],
                "rotationCenterY": costume.center[1],
            }
        )
    rehearsal = both(gt(Var(QID), REHEARSAL_IDS.start - 1), lt(Var(QID), REHEARSAL_IDS.stop))
    for block_id in switches:
        s.insert_after(
            block_id,
            when(rehearsal, Block("looks_switchcostumeto", {"COSTUME": join("R", join(Var(TEAM), join("-", Var(ROUND))))})),
        )


def team_names_from(teams: list[Any]) -> dict[int, str]:
    """チーム構成から、札に出す名前だけを取り出す。名前が決まっていない（既定の「チームN」）チームは入れない。

    メンバーの氏名は個人情報なので、画面には入れない。元の札があるのはチーム1〜5。
    """
    return {t.number: t.name for t in teams if t.name != f"チーム{t.number}" and 1 <= t.number <= len(stage_result.TEAM_COLORS)}


def _set_cloud_variables(project: dict[str, Any]) -> None:
    """裏方PCとやり取りするクラウド変数をそろえ、開いた直後の値を待機の状態にする。"""
    stage = stage_of(project)
    by_name = {v[0]: v for v in stage["variables"].values()}
    for name, default in CLOUD_DEFAULTS.items():
        if name not in by_name:
            by_name[name] = [name, default, True]
            stage["variables"][f"{Scripts.PREFIX}-cloud-{name.split()[-1]}"] = by_name[name]
        by_name[name][:] = [name, default, True]


def _drop_unused_cloud_variables(project: dict[str, Any], variant: str) -> None:
    """使わないクラウド変数を、名前から ☁ を外してふつうの変数にする。

    Packager は ☁ で始まるステージの変数をすべてクラウド変数として書き出すので、
    印を外すだけでは送れる変数のまま残る。大画面用では S2P_* もここで外し、
    回答を送る手段そのものをなくす（S2P_ANSWER に書くと出演者の回答として採点されるため）。
    """
    keep = {"☁ HEARTBEAT"} | {n for n in CLOUD_DEFAULTS if n.startswith("☁ P2S_") or variant == "play"}
    renamed = {
        var_id: value[0].removeprefix("☁ ")
        for var_id, value in stage_of(project)["variables"].items()
        if value[0].startswith("☁ ") and value[0] not in keep
    }
    rename_stage_variables(project, renamed)
    for var_id in renamed:
        del stage_of(project)["variables"][var_id][2:]  # クラウドの印も外す


def _answering(*states: int) -> Block:
    cond = eq(Var(STATE), states[0])
    for state in states[1:]:
        cond = either(cond, eq(Var(STATE), state))
    return cond


# 「通信」だけが使う変数。「見た〜」は、最後に処理した状態
COMMUNICATION_VARIABLES = ("見たSEQ", "見たANSWER", "見たSTATE", "見たTEAM", "見たROUND", "見たQID", "順に進んだ", "桁", "点", "控え", "待ち")


def _replace_communication(project: dict[str, Any], variant: str) -> None:
    """「通信」スプライトのスクリプトを入れ替える。

    ブロックの ID は作った順に振るので、下の呼び出しの順番を変えると書き出した中身（digest）が変わる。
    """
    target = find_target(project, "通信")
    target["blocks"] = {}
    target["comments"] = {}
    s = Scripts(project, "通信")
    for name in COMMUNICATION_VARIABLES:
        s.add_variable(name)
    # エキシビションの回答受付のメッセージは、今のところ受け手がない。プロジェクトにあるときだけ送る
    has_exhibition_message = "7_エキシビジョン回答受付" in stage_of(project)["broadcasts"].values()
    exhibition = [broadcast("7_エキシビジョン回答受付")] if has_exhibition_message else []

    _add_watch_loop(s)
    _define_receive_state(s)
    _define_advance(s, exhibition)
    _define_show_answer(s)
    _define_show_scores(s)
    _define_rebuild(s, exhibition, _clones_ready(project), _has_variable(stage_of(project), HIDE_GUESS))
    if variant == "play":
        _add_answer_sender(s)


def _add_watch_loop(s: Scripts) -> None:
    """旗が押されたら、P2S_SEQ と P2S_ANSWER の変化を見続ける。"""
    s.add(
        on_flag(),
        set_var("見たSEQ", "まだ"),  # 何が届いていても、最初の1回は画面を作る
        set_var("見たANSWER", "-1"),
        set_var("見たSTATE", "まだ"),
        hide_list("★入力した文字"),
        forever(
            when(
                no(eq(Var("☁ P2S_SEQ"), Var("見たSEQ"))),
                set_var("見たSEQ", Var("☁ P2S_SEQ")),
                call("状態を受け取る"),
            ),
            # 回答は届いたときに単独で送られてくる（P2S_SEQ は変わらない）
            when(
                no(eq(Var(ANSWER), Var("見たANSWER"))),
                set_var("見たANSWER", Var(ANSWER)),
                when(both(gt(Var(ANSWER), -1), _answering(3, 7)), call("回答を出す")),
            ),
        ),
    )


def _define_receive_state(s: Scripts) -> None:
    """1つ前の段階から順に進んだときだけ演出を流し、それ以外は作り直す。"""

    def step(state: int | Block, previous: int | Block, *more: Block) -> Block:
        cond = both(
            state if isinstance(state, Block) else eq(Var(STATE), state),
            previous if isinstance(previous, Block) else eq(Var("見たSTATE"), previous),
        )
        for extra in more:
            cond = both(cond, extra)
        return when(cond, set_var("順に進んだ", 1))

    same_question = eq(Var(QID), Var("見たQID"))
    was_answering = either(eq(Var("見たSTATE"), 3), eq(Var("見たSTATE"), 7))
    s.define(
        "状態を受け取る",
        set_var("順に進んだ", 0),
        when(
            eq(Var(TEAM), Var("見たTEAM")),
            step(2, 1, eq(Var(ROUND), 1)),
            step(2, 5, eq(Var(ROUND), plus(Var("見たROUND"), 1))),
            step(_answering(3, 7), 2, same_question),
            step(4, was_answering, same_question),
            step(5, 4, same_question),
            step(6, 5),
            step(8, 6),
        ),
        set_var("見たSTATE", Var(STATE)),
        set_var("見たTEAM", Var(TEAM)),
        set_var("見たROUND", Var(ROUND)),
        set_var("見たQID", Var(QID)),
        when_else(eq(Var("順に進んだ"), 1), [call("次の段階へ進める")], [call("画面を作り直す")]),
    )


def _define_advance(s: Scripts, exhibition: list[Block]) -> None:
    """1つ前の段階から進んだときに、元の演出のメッセージを送る。"""
    s.define(
        "次の段階へ進める",
        when(
            eq(Var(STATE), 2),
            set_var("回答中", 0),
            when_else(eq(Var(ROUND), 1), [broadcast("演出_風船登場開始")], [broadcast("進行_次の問題へ")]),
        ),
        when(eq(Var(STATE), 3), set_var("回答中", 1)),
        when(eq(Var(STATE), 7), set_var("回答中", 1), *exhibition),
        when(
            eq(Var(STATE), 4),
            # 採点に使われた回答にそろえる（司会の代理入力や予備の回答画面から届いた回答も出す）
            when(gt(Var(ANSWER), -1), call("回答を出す")),
            set_var("正解%", Var(CORRECT)),
            broadcast("4_正解発表"),
        ),
        when(eq(Var(STATE), 5), broadcast("5_ラウンド結果表示")),
        when(eq(Var(STATE), 6), broadcast("6_チーム結果")),
        when(eq(Var(STATE), 8), call("得点表を出す")),
    )


def _define_show_answer(s: Scripts) -> None:
    """届いた回答（☁ P2S_ANSWER）を、出演者がテンキーで入れて GO を押したときと同じ状態にする。"""
    s.define(
        "回答を出す",
        set_var("予想%", Var(ANSWER)),
        delete_all("★入力した文字"),
        set_var("桁", 0),
        repeat(
            length(Var("予想%")),
            change_var("桁", 1),
            add_to("★入力した文字", letter(Var("桁"), Var("予想%"))),
        ),
        set_var("回答中", 2),
    )


def _define_show_scores(s: Scripts) -> None:
    """全チームの得点をリスト「得点表」に入れて、全体結果の画面を出す。

    P2S_SCORES は 1 のあとにチーム番号順で3桁ずつ（protocol.encode_scores）。999 はまだのチーム。
    """
    score = join(
        join(letter(plus(Var("桁"), 1), Var(SCORES)), letter(plus(Var("桁"), 2), Var(SCORES))),
        letter(plus(Var("桁"), 3), Var(SCORES)),
    )
    s.define(
        "得点表を出す",
        delete_all("得点表"),
        set_var("桁", 1),
        repeat(
            divide(minus(length(Var(SCORES)), 1), 3),
            set_var("点", score),
            when(no(eq(Var("点"), 999)), add_to("得点表", plus(Var("点"), 0))),
            change_var("桁", 3),
        ),
        broadcast(stage_result.SHOW_MESSAGE),  # 全体結果の画面は stage_result.py のスプライトが出す
    )


def _clones_ready(project: dict[str, Any]) -> list[Block]:
    """初期化 のあと、スプライト UI が札や風船の役割のクローンを作り終えるまで待つブロック。

    UI は 初期化 で役割 1〜8 のクローンを1コマに1つずつ作り、最後に元のスプライトの 役割 を -1 にする。
    作り終える前に 1_チーム紹介 などを送ると、札のクローン（役割 7）がまだなく、作っている途中の元の
    スプライトが札を出してしまい、次の段階で消える。重い端末ではコマが遅く、決まった秒数では足りない。
    Scratch 側の作りが変わって -1 にならなくても止まらないよう、3秒ほどで待つのをやめる。
    """
    if not _has_variable(_optional_target(project, "UI"), "役割"):
        return []
    return [
        set_var("待ち", 0),
        repeat_until(either(eq(of_sprite("UI", "役割"), -1), gt(Var("待ち"), 30)), wait(0.1), change_var("待ち", 1)),
    ]


def _define_rebuild(s: Scripts, exhibition: list[Block], clones_ready: list[Block], hide_guess: bool) -> None:
    """今の状態だけから画面を組み立てる。開き直したとき・送り直されたとき・チームが替わるときに通る。

    hide_guess は、元のプロジェクトに 予想%消して があるか（あれば、ゲームオーバー・クリアの正解発表で 1 にする）。
    """
    guess_shown = [set_var(HIDE_GUESS, 0)] if hide_guess else []
    guess_hidden = [set_var(HIDE_GUESS, 1)] if hide_guess else []
    s.define(
        "画面を作り直す",
        broadcast("作り直し_止める"),
        wait(0.1),  # 止め終わるのを待つ。同じコマで 初期化 を送ると、その受け手まで止めてしまう
        Block("sound_stopallsounds"),
        set_var("復帰描画中", 0),
        set_var("エキシビション", 0),
        set_var("途中復帰", 0),
        when(gt(Var(STATE), 1), set_var("途中復帰", 1)),  # 1 のあいだはタイトルのロゴを出さない
        delete_all("★入力した文字"),
        *guess_shown,
        hide_list("得点表"),
        broadcast("初期化"),
        wait(0.5),
        *clones_ready,
        when(
            gt(Var(STATE), 0),
            set_var("風船の数", Var(BALLOONS)),
            wait(0.1),
            broadcast("1_チーム紹介"),
            when(eq(Var(STATE), 8), call("得点表を出す")),
            when(
                both(gt(Var(STATE), 1), lt(Var(STATE), 8)),
                when(lt(Var(BALLOONS), 1), set_var("エキシビション", 1)),
                set_var("正解%", Var(CORRECT)),
                set_var("復帰描画中", 1),
                set_var("回答中", 0.1),  # 0 のままだと数字のクローンが出ない
                broadcast("途中復帰_背景同期"),
                broadcast("途中復帰_出題UI同期"),
                when(eq(Var(STATE), 2), broadcast(QUESTION_HIDE_GAUGE_MESSAGE)),  # 出題のあいだはゲージが出ていない
                # ゲームオーバーになった問題のあいだは、「エキシビションモード」の表示はまだ出ていない（次の問題から出る）
                when(eq(Var(FAILED_ROUND), Var(ROUND)), broadcast(EXHIBITION_LABEL_HIDE_MESSAGE)),
                wait(0.6),
                broadcast("途中復帰_入力監視開始"),
                when(lt(Var(BALLOONS), 1), broadcast("作り直し_ゲームオーバー後")),
                when(
                    _answering(3, 7),
                    set_var("回答中", 1),
                    *([when(eq(Var(STATE), 7), *exhibition)] if exhibition else []),
                    when(gt(Var(ANSWER), -1), call("回答を出す")),
                ),
                when(
                    both(gt(Var(STATE), 3), lt(Var(STATE), 7)),
                    when(gt(Var(ANSWER), -1), call("回答を出す")),
                    set_var("誤差", absolute(minus(Var("予想%"), Var("正解%")))),
                    set_var("控え", Var("エキシビション")),
                    set_var("エキシビション", 1),  # 風船を割る鳥を出さない
                    broadcast("4_正解発表"),
                    wait(0.3),
                    broadcast("5_ラウンド結果表示"),
                    wait(0.1),
                    set_var("エキシビション", Var("控え")),
                    Block("sound_stopallsounds"),
                    when_else(
                        eq(Var(ROUND), 5),
                        # 最後の問題のあとは音楽が止まり、クリアなら背景が変わる
                        [
                            when(
                                gt(Var(BALLOONS), 0),
                                Block("looks_switchbackdropto", {"BACKDROP": Menu("looks_backdrops", "BACKDROP", "beach")}),
                            )
                        ],
                        [broadcast("演出_バルーン破裂完了")],  # 音楽を戻す
                    ),
                    when(eq(Var(STATE), 6), wait(0.1), broadcast("6_チーム結果")),
                    # その場かぎりの演出で出ていた文字（GAME OVER はこの問題でなったときだけ。CLEARED はチーム結果で消える）
                    when(eq(Var(FAILED_ROUND), Var(ROUND)), broadcast(GAME_OVER_TEXT_MESSAGE)),
                    when(
                        both(both(eq(Var(ROUND), 5), gt(Var(BALLOONS), 0)), lt(Var(STATE), 6)),
                        broadcast(CLEARED_TEXT_MESSAGE),
                    ),
                    when(
                        both(both(eq(Var(STATE), 4), lt(Var(BALLOONS), 1)), no(eq(Var(FAILED_ROUND), Var(ROUND)))),
                        broadcast(EXHIBITION_LABEL_SHOW_MESSAGE),  # エキシビションの正解発表（ラウンド結果で隠れた）
                    ),
                    # その問題でゲームオーバー・クリアになった正解発表では、元の演出が問題文などを隠し、回答中 を 4 にしている
                    # （ラウンド結果でまた出す）。上で ラウンド結果 まで出したので、同じようにし直す
                    when(
                        eq(Var(STATE), 4),
                        when(
                            eq(Var(FAILED_ROUND), Var(ROUND)),
                            set_var("回答中", 4),  # ゲームオーバーのあとの「残り」は 回答中 が 3 のときだけ出る
                            *guess_hidden,
                            broadcast(GAME_OVER_HIDE_MESSAGE),
                        ),
                        when(
                            both(eq(Var(ROUND), 5), gt(Var(BALLOONS), 0)),
                            set_var("回答中", 4),
                            *guess_hidden,
                            broadcast(CLEARED_HIDE_MESSAGE),
                        ),
                    ),
                ),
                wait(0.1),
                set_var("復帰描画中", 0),
            ),
        ),
        set_var("途中復帰", 0),
    )


def _add_answer_sender(s: Scripts) -> None:
    """出演者用: GO が押されたら、回答を裏方PCへ送る（ANSWER → SEQ の順）。"""
    s.add(
        on_message("回答_確定"),
        set_var("☁ S2P_ANSWER", Var("予想%")),
        change_var("☁ S2P_SEQ", 1),
    )


def _add_rebuild_helpers(project: dict[str, Any], notes: list[str]) -> None:
    """作り直しのときに要る受け手を、演出のスプライトに足す。"""
    names = {t["name"] for t in project["targets"]}
    _add_rebuild_game_over_text(project, notes)
    _add_rebuild_cleared_text(project, notes)
    _mirror_simple_receivers(project, "結果_ゲームオーバー", GAME_OVER_HIDE_MESSAGE)
    _mirror_simple_receivers(project, "結果_クリア", CLEARED_HIDE_MESSAGE)

    # エキシビションの回答受付で開き直したときも音楽を戻す（元の 途中復帰_出題UI同期 は出題と回答受付のときだけ）
    stage = stage_of(project)
    if _has_variable(stage, "再生中") and any(
        b.get("mutation", {}).get("proccode") == "1を再生" for b in blocks_of(stage).values()
    ):
        Scripts(project, "Stage").add(
            on_message("途中復帰_出題UI同期"),
            when(both(eq(Var(STATE), 7), eq(Var("再生中"), 0)), call("1を再生")),
        )
    else:
        notes.append("エキシビションの回答受付で開き直したときの音楽（#59）は戻しません（ステージに「1を再生」がない）")

    # 動いている途中の演出（ゲージ・風船を割る数え下げ・音楽の繰り返し）を止める
    for name in ("Stage", "調整・制御", "正解", "GameOver"):
        if name == "Stage" or name in names:
            Scripts(project, name).add(on_message("作り直し_止める"), stop_other_scripts())

    # 元の受け手は問題文の絵を切り替えないので、保存されたときの絵のまま出てしまう
    Scripts(project, "問題").add(
        on_message("途中復帰_出題UI同期"),
        Block("looks_setsizeto", {"SIZE": 100}),
        Block("looks_switchcostumeto", {"COSTUME": join(Var(TEAM), join("-", Var(ROUND)))}),
    )

    # 残り0の「0」は、クローンができた場所にそのまま出る（1桁の数字を経ていれば残りの風船の上にいる）
    if "数字" in names:
        Scripts(project, "数字").add(
            on_message("途中復帰_出題UI同期"), Block("motion_gotoxy", {"X": 200, "Y": -80})
        )

    # ゲームオーバーのあとは、残りの風船を正解発表のときだけ出す（元は ゲームオーバーの演出の中で始めている）
    if _has_variable(_optional_target(project, "UI"), "役割"):
        Scripts(project, "UI").add(
            on_message("作り直し_ゲームオーバー後"),
            when(
                eq(Var("役割"), 5),
                forever(when_else(eq(Var("回答中"), 3), [Block("looks_show")], [Block("looks_hide")])),
            ),
        )

    # 元の 途中復帰_出題UI同期 は、出題の段階でもゲージ（UI_オーバーレイ の役割2）を出す。順に進んだときは、
    # ゲージは回答受付で初めて出るので、出題の段階では隠し直す。10/18 の版では絵がクローンを作ったときのまま
    # （コスチューム1）なので、絵の名前では見分けられない
    if _has_variable(_optional_target(project, "UI_オーバーレイ"), "役割"):
        Scripts(project, "UI_オーバーレイ").add(
            on_message(QUESTION_HIDE_GAUGE_MESSAGE), when(eq(Var("役割"), 2), Block("looks_hide"))
        )

    # 「エキシビションモード」の表示（10/13 の版から）。元の 途中復帰_出題UI同期 は、残りが0なら出す。
    # 順に進んだときは次の問題（進行_次の問題へ）で初めて出るので、ゲームオーバーになった問題のあいだは隠し直す。
    # 10/18 の版からはラウンド結果で隠すので、組み立て直しでラウンド結果まで送るエキシビションの正解発表では出し直す
    label_role = _exhibition_label_role(project)
    if label_role is not None:
        is_label = eq(Var("役割"), label_role)
        ui = Scripts(project, "UI")
        ui.add(on_message(EXHIBITION_LABEL_HIDE_MESSAGE), when(is_label, Block("looks_hide")))
        ui.add(on_message(EXHIBITION_LABEL_SHOW_MESSAGE), when(is_label, Block("looks_show")))

    # GAME OVER の文字はクローンのまま残り、次のチームになっても消えない
    if "GameOver" in names:
        Scripts(project, "GameOver").add(
            on_message("初期化"), Block("looks_hide"), Block("control_delete_this_clone")
        )


def _exhibition_label_role(project: dict[str, Any]) -> str | None:
    """「エキシビションモード」の表示を受け持つ、スプライト UI のクローンの役割（なければ None）。

    「もし 役割 = N なら … コスチュームを エキシビションモード にする」の N。絵の名前では見分けられない。
    何もしない役割のクローンも、元のスプライトの絵（保存したときの絵が エキシビションモード）を引き継いでいるため。
    """
    ui = _optional_target(project, "UI")
    if ui is None:
        return None
    blocks = blocks_of(ui)
    for block_id, block in blocks.items():
        if block["opcode"] != "looks_switchcostumeto" or not _is_simple_look(blocks, block):
            continue
        if _copy_simple_look(blocks, block).inputs["COSTUME"].value != "エキシビションモード":
            continue
        enclosing = _enclosing_if(blocks, block_id)
        role = _role_condition(blocks, enclosing) if enclosing is not None else None
        if role is not None:
            return role
    return None


def _enclosing_if(blocks: dict[str, Any], block_id: str) -> dict[str, Any] | None:
    """block_id を中に持つ「もし」のブロック（なければ None）。前のブロックをたどって、並びの先頭の親を見る。"""
    while True:
        parent_id = blocks[block_id]["parent"]
        if parent_id is None:
            return None
        parent = blocks[parent_id]
        if parent["next"] != block_id:  # 前のブロックではなく、中に持っている親
            return parent if parent["opcode"] == "control_if" else None
        block_id = parent_id


GAME_OVER_TEXT_MESSAGE = "作り直し_ゲームオーバー文字"
CLEARED_TEXT_MESSAGE = "作り直し_クリア文字"
GAME_OVER_HIDE_MESSAGE = "作り直し_ゲームオーバーで隠す"
CLEARED_HIDE_MESSAGE = "作り直し_クリアで隠す"
QUESTION_HIDE_GAUGE_MESSAGE = "作り直し_出題でゲージを隠す"
EXHIBITION_LABEL_HIDE_MESSAGE = "作り直し_エキシビション表示を隠す"
EXHIBITION_LABEL_SHOW_MESSAGE = "作り直し_エキシビション表示を出す"


def _mirror_simple_receivers(project: dict[str, Any], source: str, message: str) -> None:
    """source を受ける「隠す・絵を替えるだけ」のスクリプトを、message でも同じように動くようにする。

    元の演出は、結果_ゲームオーバー・結果_クリア で問題文や問題枠を隠す（音や動きのある受け手もある）。
    開き直したときは、そのうち「隠す・絵を替える・最前面へ出すだけ」「役割が N なら同じことをするだけ」のものだけを、
    音と動きなしでやり直したい。形が違う受け手（音・待ち・動き）は写さない。
    """
    for target, top_id in receivers(project, source):
        blocks = blocks_of(target)
        looks = _simple_looks(blocks, blocks[top_id]["next"])
        if looks:
            Scripts(project, target["name"]).add(on_message(message), *looks)
            continue
        first = blocks.get(blocks[top_id]["next"] or "")
        if first is None or first["next"]:
            continue
        role = _role_condition(blocks, first)
        looks = _simple_looks(blocks, first["inputs"].get("SUBSTACK", [None, None])[1])
        if role is not None and looks:
            Scripts(project, target["name"]).add(on_message(message), when(eq(Var("役割"), role), *looks))


def _simple_looks(blocks: dict[str, Any], block_id: str | None) -> list[Block]:
    """block_id から続くブロックが、すべて「隠す・絵を替える・最前面へ出す」なら、その写しを返す（違えば空）。"""
    looks: list[Block] = []
    while block_id:
        block = blocks.get(block_id)
        if block is None or not _is_simple_look(blocks, block):
            return []
        looks.append(_copy_simple_look(blocks, block))
        block_id = block["next"]
    return looks


def _is_simple_look(blocks: dict[str, Any], block: dict[str, Any]) -> bool:
    if block["opcode"] in ("looks_hide", "looks_gotofrontback"):
        return True
    if block["opcode"] != "looks_switchcostumeto":
        return False
    menu = blocks.get(block["inputs"].get("COSTUME", [None, None])[1] or "")
    return isinstance(menu, dict) and menu["opcode"] == "looks_costume"


def _copy_simple_look(blocks: dict[str, Any], block: dict[str, Any]) -> Block:
    if block["opcode"] == "looks_hide":
        return Block("looks_hide")
    if block["opcode"] == "looks_gotofrontback":
        return Block("looks_gotofrontback", fields={"FRONT_BACK": block["fields"]["FRONT_BACK"][0]})
    costume = blocks[block["inputs"]["COSTUME"][1]]["fields"]["COSTUME"][0]
    return Block("looks_switchcostumeto", {"COSTUME": Menu("looks_costume", "COSTUME", costume)})


def _role_condition(blocks: dict[str, Any], block: dict[str, Any]) -> str | None:
    """「もし（役割）= N なら」の N（そうでなければ None）。"""
    if block["opcode"] != "control_if":
        return None
    cond = blocks.get(block["inputs"].get("CONDITION", [None, None])[1] or "")
    if cond is None or cond["opcode"] != "operator_equals":
        return None
    a, b = cond["inputs"].get("OPERAND1"), cond["inputs"].get("OPERAND2")
    for var, value in ((a, b), (b, a)):
        if isinstance(var, list) and isinstance(var[1], list) and var[1][:2] == [12, "役割"] and _literal(value) is not None:
            return _literal(value)
    return None


def _literal(value: Any) -> str | None:
    """入力に直接書かれた値（ブロックが入っていれば None）。"""
    if isinstance(value, list) and len(value) > 1 and isinstance(value[1], list) and value[1][0] in (4, 5, 6, 7, 8, 10):
        return str(value[1][1])
    return None


def _add_rebuild_game_over_text(project: dict[str, Any], notes: list[str]) -> None:
    """開き直したときに、その問題で出ていた「GAME OVER」の文字を、音と待ちなしで並べ直す（#59）。

    元の演出（スプライト GameOver が 結果_ゲームオーバー で並べる）から、文字・大きさ・位置・間隔を読み取る。
    文字はクローンで出て、クローンになったときの演出（大きくなる）は元のものがそのまま動く。
    """
    found = [(t, top) for t, top in receivers(project, "結果_ゲームオーバー") if t["name"] == "GameOver"]
    if not found:
        notes.append("開き直したときの GAME OVER の文字（#59）は出しません（スプライト GameOver が 結果_ゲームオーバー を受けていない）")
        return
    target, top_id = found[0]
    blocks = blocks_of(target)
    size = start = None
    letters: list[str] = []
    proccode = None
    for block_id in script_blocks(target, top_id):
        block = blocks[block_id]
        if block["opcode"] == "looks_setsizeto":
            size = _literal(block["inputs"].get("SIZE"))
        elif block["opcode"] == "motion_gotoxy":
            start = (_literal(block["inputs"].get("X")), _literal(block["inputs"].get("Y")))
        elif block["opcode"] == "procedures_call":
            values = [_literal(v) for v in block["inputs"].values()]
            if len(values) == 1 and values[0] is not None:
                letters.append(values[0])
                proccode = block["mutation"]["proccode"]
    step = None
    for block_id, block in blocks.items():
        if block["opcode"] == "procedures_prototype" and block["mutation"]["proccode"] == proccode:
            for inner in script_blocks(target, block["parent"]):
                if blocks[inner]["opcode"] == "motion_changexby":
                    step = _literal(blocks[inner]["inputs"].get("DX"))
    if not letters or size is None or start is None or None in start or step is None:
        notes.append("開き直したときの GAME OVER の文字（#59）は出しません（元の演出から文字の並べ方を読み取れない）")
        return
    body = [Block("looks_setsizeto", {"SIZE": size}), Block("motion_gotoxy", {"X": start[0], "Y": start[1]})]
    for letter_name in letters:
        body += [
            Block("looks_switchcostumeto", {"COSTUME": letter_name}),
            create_clone(),
            Block("motion_changexby", {"DX": step}),
        ]
    Scripts(project, "GameOver").add(on_message(GAME_OVER_TEXT_MESSAGE), *body)


def _add_rebuild_cleared_text(project: dict[str, Any], notes: list[str]) -> None:
    """開き直したときに、最後の問題のあとに出ていた「CLEARED」の文字を、音と動きなしで出す（#59）。

    元は スプライト UI_結果演出 の 役割 1 のクローンが、結果_クリア でコスチューム Perfect_cleared を大きくしながら出す。
    """
    target = _optional_target(project, "UI_結果演出")
    if not _has_variable(target, "役割") or not any(c["name"] == "Perfect_cleared" for c in target["costumes"]):
        notes.append("開き直したときの CLEARED の文字（#59）は出しません（UI_結果演出 の 役割 か Perfect_cleared がない）")
        return
    Scripts(project, "UI_結果演出").add(
        on_message(CLEARED_TEXT_MESSAGE),
        when(
            eq(Var("役割"), 1),
            Block("looks_switchcostumeto", {"COSTUME": "Perfect_cleared"}),
            Block("looks_setsizeto", {"SIZE": 100}),
            Block("looks_cleargraphiceffects"),
            Block("looks_show"),
        ),
    )


def _keep_keypad_on_first_question(project: dict[str, Any], notes: list[str]) -> None:
    """各チームの1問目で、早く回答受付に進めてもテンキーが消えないようにする（#53）。

    元は、登場の効果音（約8秒）を鳴らし終えたあとで 回答中 を 0.1 に戻している。
    その前に回答受付（回答中=1）へ進んでいると上書きされ、テンキーが出なくなる。
    まだ 0 のときだけ 0.1 にする。
    """
    for target, top_id in receivers(project, "演出_風船登場開始"):
        blocks = blocks_of(target)
        for block_id in script_blocks(target, top_id):
            block = blocks[block_id]
            if (
                block["opcode"] == "data_setvariableto"
                and block["fields"]["VARIABLE"][0] == "回答中"
                and block["inputs"]["VALUE"][1][1:] == ["0.1"]
            ):
                Scripts(project, target["name"]).wrap_in_if(block_id, eq(Var("回答中"), 0))
                return
    notes.append("1問目のテンキーの直し（#53）は当てていません（効果音のあとで 回答中 を 0.1 にするブロックが見つからない）")


def _show_first_question_with_start_sound(project: dict[str, Any], notes: list[str]) -> None:
    """各チームの1問目で、登場の効果音を鳴らし始めたらすぐ問題文を出す（#59）。

    元は効果音（約8秒）を鳴らし終えてから 出題_開始 を送るので、問題文が出るまで待たされる。
    鳴らし始めたらすぐ送るようにし、出題_開始 で始まるステージの音楽は、効果音と重ならないよう
    効果音の長さだけ待ってから始める（正解発表や作り直しで止まれば、待っている音楽も始まらない）。
    """
    for target, top_id in receivers(project, "演出_風船登場開始"):
        blocks = blocks_of(target)
        for block_id in script_blocks(target, top_id):
            block = blocks[block_id]
            after = blocks.get(block["next"] or "")
            if block["opcode"] != "sound_playuntildone" or not after or _broadcast_name(after) != "出題_開始":
                continue
            seconds = _sound_seconds(target, block)
            music = [b for t, b in receivers(project, "出題_開始") if t["isStage"]]
            if seconds is None or not music:
                break
            block["opcode"] = "sound_play"
            for hat_id in music:
                Scripts(project, "Stage").insert_after(hat_id, wait(seconds))
            return
    notes.append("1問目を効果音と同時に出す直し（#59）は当てていません（効果音を鳴らし終えてから 出題_開始 を送るブロックが見つからない）")


def _broadcast_name(block: dict[str, Any]) -> str | None:
    if block["opcode"] not in ("event_broadcast", "event_broadcastandwait"):
        return None
    value = block["inputs"].get("BROADCAST_INPUT", [None, None])[1]
    return value[1] if isinstance(value, list) and value[0] == 11 else None


def _sound_seconds(target: dict[str, Any], block: dict[str, Any]) -> float | None:
    """「終わるまで〜の音を鳴らす」の音の長さ（秒）。音を名前で選んでいないときは None。"""
    menu = target["blocks"].get(block["inputs"].get("SOUND_MENU", [None, None])[1] or "")
    if not isinstance(menu, dict):
        return None
    name = menu["fields"].get("SOUND_MENU", [None])[0]
    for sound in target["sounds"]:
        if sound["name"] == name and sound.get("rate") and sound.get("sampleCount"):
            return round(sound["sampleCount"] / sound["rate"], 2)
    return None


# ---- 書き出し --------------------------------------------------------


def digest(project: dict[str, Any]) -> str:
    """作った画面の中身を表す値。本番と手元の照合に使う。"""
    data = json.dumps(project, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


ASSET_URL = "https://assets.scratch.mit.edu/internalapi/asset/{md5ext}/get/"
ASSET_CACHE = PACKAGER_DIR / ".assets"  # 元のプロジェクトの絵と音。中身で名前が決まるので、一度取れば使い回せる


def _fetch_asset(md5ext: str) -> bytes:
    cached = ASSET_CACHE / md5ext
    if cached.exists():
        return cached.read_bytes()
    req = urllib.request.Request(ASSET_URL.format(md5ext=md5ext), headers={"User-Agent": "suzuleague-stage"})
    with urllib.request.urlopen(req, timeout=120) as res:
        data = res.read()
    ASSET_CACHE.mkdir(exist_ok=True)
    cached.write_bytes(data)
    return data


def build_sb3(project: dict[str, Any], extra_assets: dict[str, bytes], fetch=_fetch_asset) -> bytes:
    """project.json と素材を1つの sb3（zip）にまとめる。

    ここで足した素材（extra_assets）は Scratch のサーバにないので、Packager に project.json だけを
    渡すと取ってこられない。元の素材も一緒に入れた sb3 を渡す。
    """
    names = sorted({a["md5ext"] for t in project["targets"] for a in (*t["costumes"], *t["sounds"])})
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("project.json", json.dumps(project, ensure_ascii=False))
        for name in names:
            archive.writestr(name, extra_assets[name] if name in extra_assets else fetch(name))
    return data.getvalue()


def _run_packager(sb3: bytes, room_id: str, title: str) -> bytes:
    """TurboWarp Packager で、HTML と素材をまとめた zip を作る。"""
    if not (PACKAGER_DIR / "node_modules").is_dir():
        print("  TurboWarp Packager を入れています（最初の1回だけ）…")
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=PACKAGER_DIR, check=True)
    with tempfile.TemporaryDirectory() as tmp:
        source, packed = Path(tmp) / "project.sb3", Path(tmp) / "stage.zip"
        source.write_bytes(sb3)
        subprocess.run(
            ["node", str(PACKAGER_DIR / "pack.js"), str(source), str(packed), room_id, title],
            cwd=PACKAGER_DIR,
            check=True,
        )
        return packed.read_bytes()


# Packager は接続先を決め打ちで書き込む。観客ページと同じく「このページを配っているサーバ」に
# つなぐよう書き換えると、本番（Render）・手元のサーバ・代替手段のどれでもそのまま動く
CLOUD_HOST_PLACEHOLDER = "wss://stage.invalid"
CLOUD_HOST_EXPRESSION = '(location.protocol === "https:" ? "wss://" : "ws://") + location.host'


def write_variant(packed: bytes, out_dir: Path, version: dict[str, str]) -> None:
    """zip を展開して out_dir を置き換える。"""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        archive.extractall(out_dir)
    index = out_dir / "index.html"
    html = index.read_text(encoding="utf-8")
    literal = json.dumps(CLOUD_HOST_PLACEHOLDER)
    if html.count(literal) != 1:
        raise StageError("書き出した index.html に接続先が見つかりません（Packager の出力が変わった）")
    index.write_text(html.replace(literal, CLOUD_HOST_EXPRESSION), encoding="utf-8")
    (out_dir / "version.json").write_text(json.dumps(version, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(
    server_dir: Path, room_id: str, project_id: str = SCRATCH_PROJECT_ID, team_names: dict[int, str] | None = None
) -> None:
    public = server_dir / "public"
    if not public.is_dir():
        raise FileNotFoundError(f"{public} がありません（cloud-server の場所を --server-dir で指定）")
    project, modified = fetch_project(project_id)
    print(f"元のプロジェクト: {project_id}（最終更新 {modified}）")
    for variant, label in VARIANTS.items():
        made, notes = transform(project, variant, team_names)
        for note in notes:
            print(f"  ! {note}")
        print(f"  {label} を書き出しています…")
        sb3 = build_sb3(made, stage_result.assets(team_names))
        packed = _run_packager(sb3, room_id, f"スズリーグ ステージ {label}")
        version = {"source": project_id, "source_modified": modified, "room": room_id, "digest": digest(made)}
        write_variant(packed, public / "stage" / variant, version)
        print(f"  → public/stage/{variant}/")
    print(f"→ {server_dir} で差分を確認してコミットし、master に入れると Render がデプロイします")


def add_rehearsal(server_dir: Path) -> None:
    """書き出してある画面（public/stage/）に、リハーサル用の問題文の絵と切り替えだけを足す。

    2026-10-30、本番の2日前に足した。配信中の画面は Scratch の 10/18 の版から作ったもので、
    Scratch 側は 10/25 にも更新されているが、本番の直前に取り込むと確かめきれない。
    書き出した画面は assets/project.json と絵を開いたときに読むので、そこだけを書き換える。
    """
    for variant, label in VARIANTS.items():
        out_dir = server_dir / "public" / "stage" / variant
        project_file = out_dir / "assets" / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        if any(c["name"].startswith("R") for c in find_target(project, "問題")["costumes"]):
            print(f"  {label}: すでに足してあります")
            continue
        _add_rehearsal_questions(project)
        project_file.write_text(json.dumps(project, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        for costume in stage_result.rehearsal_questions():
            (out_dir / "assets" / costume.md5ext).write_bytes(costume.data)
        version_file = out_dir / "version.json"
        version = json.loads(version_file.read_text(encoding="utf-8"))
        version |= {"digest": digest(project), "rehearsal": "added"}
        version_file.write_text(json.dumps(version, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  → public/stage/{variant}/ にリハーサル用の問題文を足しました")
    print(f"→ {server_dir} で差分を確認してコミットし、master に入れると Render がデプロイします")


def check(
    base_url: str, room_id: str, project_id: str = SCRATCH_PROJECT_ID, team_names: dict[int, str] | None = None
) -> list[str]:
    """本番で配信中の画面が、今の Scratch プロジェクトから作ったものと同じか。食い違いを返す。"""
    project, modified = fetch_project(project_id)
    problems = []
    for variant, label in VARIANTS.items():
        made, _ = transform(project, variant, team_names)
        url = f"{base_url.rstrip('/')}/stage/{variant}/version.json"
        try:
            live = _get_json(url)
        except (urllib.error.URLError, OSError, ValueError) as e:
            problems.append(f"{label}: 取得できません（{e}）")
            continue
        if live.get("room") != room_id:
            problems.append(f"{label}: ルームIDが違います（本番 {live.get('room')}）")
        elif live.get("digest") != digest(made):
            newer = (
                "Scratch 側が更新されています"
                if live.get("source_modified") != modified
                else "作り方かチーム名が変わっています"
            )
            problems.append(f"{label}: 本番の中身が手元から作ったものと違います（{newer}）")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scratch のステージ画面を作り直して cloud-server に書き出す")
    parser.add_argument("--room-id", default=SCRATCH_PROJECT_ID, help="接続先のルームID（観客ページと同じもの）")
    parser.add_argument("--server-dir", type=Path, default=DEFAULT_SERVER_DIR, help="cloud-server のクローン先")
    parser.add_argument("--check", action="store_true", help="書き出さずに、本番で配信中の画面と照合する")
    parser.add_argument(
        "--add-rehearsal", action="store_true", help="書き出してある画面に、リハーサル用の問題文だけを足す"
    )
    parser.add_argument("--url", default=PRODUCTION_URL, help="--check で照合する先")
    parser.add_argument(
        "--teams",
        type=Path,
        default=DEFAULT_TEAMS,
        help="チーム名を読むチーム構成（既定はリポジトリ直下の teams.json。なければ「チームN」のまま）",
    )
    args = parser.parse_args(argv)

    if args.add_rehearsal:
        add_rehearsal(args.server_dir)
        return 0
    try:
        team_names = team_names_from(load_teams(args.teams)) if args.teams.exists() else {}
        print(f"チーム名: {'・'.join(team_names.values()) if team_names else '（入れない。「チームN」のまま）'}")
        if args.check:
            problems = check(args.url, args.room_id, team_names=team_names)
            for p in problems:
                print(f"✗ {p}")
            if problems:
                print("→ uv run python -m suzuleague.stage で作り直し、cloud-server にコミットしてください")
                return 1
            print(f"✓ {args.url} のステージ画面 {len(VARIANTS)} 件は、今の Scratch プロジェクトから作ったものと一致しています")
            return 0
        build(args.server_dir, args.room_id, team_names=team_names)
    except (ProjectError, ValueError) as e:
        print(f"✗ {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

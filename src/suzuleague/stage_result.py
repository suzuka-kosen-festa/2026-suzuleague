"""ステージ画面の全体結果（ステート8）の画面。

元のプロジェクトの得点表は、チーム5つぶんの空のマスの上に、Scratch のリスト表示が重なるだけだった。
大画面に映して表彰式で見せるので、ここで作ったスプライト「結果発表」に置き換える。

- 絵は SVG をここで組み立てる（元のプロジェクトにはない素材。sb3 に一緒に入れて書き出す）
- 点数は、スプライト「通信」がリスト「得点表」にチーム番号順で入れてから「結果_出す」を送る
- チームの数はリストの長さで決まる（本番は4、デモは1）。カードを1枚ずつ出し、最後に優勝を示す

優勝の決め方は engine.winners() と同じ（残りバルーンが最多のチーム。同数なら全チーム。全チームが0なら優勝なし）。
"""

from __future__ import annotations

from html import escape
from typing import Any

from .sb3 import (
    Block,
    Costume,
    Scripts,
    Var,
    add_sprite,
    both,
    change_var,
    create_clone,
    divide,
    eq,
    gt,
    item,
    join,
    length,
    letter,
    list_length,
    lt,
    minus,
    on_clone_start,
    on_message,
    plus,
    repeat,
    set_var,
    stop_other_scripts,
    times,
    wait,
    when,
    when_else,
)

SPRITE = "結果発表"
SHOW_MESSAGE = "結果_出す"
SCORES_LIST = "得点表"

# 元のプロジェクトの得点表・チーム名の札と同じ色（チーム1〜5）
TEAM_COLORS = ("#ff7fcc", "#80d1ff", "#9cff82", "#ffef82", "#ff8282")
RANK_COLORS = ("#ffd23f", "#d9dee8", "#e0a36b", "#9fb2d9", "#9fb2d9")

NAVY = "#10244f"
# 日本語の文字。Scratch に入っているフォントは英数字だけなので、端末のゴシック体で出す
JP = 'font-family="&quot;Hiragino Sans&quot;, &quot;Yu Gothic&quot;, Meiryo, &quot;Noto Sans JP&quot;, sans-serif" font-weight="700"'
# 英数字。Scratch のフォント名を書くと、どの端末でも同じ字形になる
LATIN = 'font-family="Sans Serif"'
# 数字。元の画面の「残り」の数字と同じ書き方（白い字に黒い縁）
DIGIT = 'font-family="Handwriting"'

CARD_W, CARD_H = 104, 210
CARD_Y = -35  # カードの中心（ステージの座標）
CARD_PITCH = 114  # カードどうしの中心の間隔


def _svg(width: int, height: int, body: str) -> str:
    # 透明の四角を敷いて、絵の大きさを文字の幅に左右されないようにする
    return (
        f'<svg version="1.1" xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}"><rect width="{width}" height="{height}" fill="#000000" fill-opacity="0"/>'
        f"{body}</svg>"
    )


def _costume(name: str, width: int, height: int, body: str) -> Costume:
    return Costume(name, _svg(width, height, body), (width / 2, height / 2))


def _background() -> Costume:
    balloons = "".join(
        f'<line x1="{x}" y1="{y + r}" x2="{x + 4}" y2="{y + r + 46}" stroke="#ffffff" stroke-opacity="0.25" stroke-width="1.5"/>'
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}" fill-opacity="0.55"/>'
        f'<circle cx="{x - r * 0.3:.1f}" cy="{y - r * 0.35:.1f}" r="{r * 0.22:.1f}" fill="#ffffff" fill-opacity="0.45"/>'
        for x, y, r, color in (
            (34, 300, 22, TEAM_COLORS[0]),
            (70, 326, 17, TEAM_COLORS[3]),
            (448, 304, 21, TEAM_COLORS[1]),
            (412, 330, 16, TEAM_COLORS[2]),
            (24, 96, 14, TEAM_COLORS[4]),
            (458, 104, 15, TEAM_COLORS[3]),
        )
    )
    body = (
        '<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#0c2f7a"/><stop offset="1" stop-color="#2f86e0"/></linearGradient></defs>'
        '<rect width="480" height="360" fill="url(#sky)"/>'
        f"{balloons}"
        # 元の画面の問題枠と同じ、青と橙の二重の枠
        '<rect x="118" y="10" width="244" height="46" rx="6" fill="#ffffff" stroke="#0a50e6" stroke-width="5"/>'
        '<rect x="124" y="16" width="232" height="34" rx="3" fill="none" stroke="#ff8c1a" stroke-width="2.5"/>'
        f'<text x="240" y="43" text-anchor="middle" font-size="26" letter-spacing="6" fill="{NAVY}" {JP}>結果発表</text>'
    )
    return _costume("背景", 480, 360, body)


def _text_width(text: str, font_size: float) -> float:
    """文字の幅の見積もり（日本語は1文字ぶん、英数字はその6割）。端末のフォントで出すので目安。"""
    return sum(1.0 if ord(c) > 0x2E7F else 0.6 for c in text) * font_size


def _fitting_size(text: str, largest: float, width: float) -> float:
    """width に収まる文字の大きさ（largest より大きくはしない）。"""
    return round(min(largest, width / max(_text_width(text, 1), 1)), 1)


def _card(team: int, name: str | None = None) -> Costume:
    color = TEAM_COLORS[team - 1]
    if name:
        # チーム名が決まっていれば、番号の代わりに名前を出す（長い名前は縮める）
        size = _fitting_size(name, 17, CARD_W - 14)
        title = f'<text x="{CARD_W / 2}" y="{26 + size * 0.36:.1f}" text-anchor="middle" font-size="{size}" fill="#1a1a1a" {JP}>{escape(name)}</text>'
    else:
        title = (
            f'<text x="34" y="33" text-anchor="middle" font-size="13" fill="#1a1a1a" {JP}>チーム</text>'
            f'<text x="76" y="38" text-anchor="middle" font-size="30" fill="#1a1a1a" {LATIN}>{team}</text>'
        )
    body = (
        f'<rect x="2.5" y="2.5" width="{CARD_W - 5}" height="{CARD_H - 5}" rx="10" fill="{NAVY}" stroke="#ffffff" stroke-width="3"/>'
        f'<path d="M4,48 V13 a9,9 0 0 1 9,-9 H{CARD_W - 13} a9,9 0 0 1 9,9 V48 Z" fill="{color}"/>'
        f"{title}"
        f'<text x="{CARD_W / 2}" y="68" text-anchor="middle" font-size="10.5" fill="#b9c8ef" {JP}>残りバルーン</text>'
    )
    return _costume(f"カード{team}", CARD_W, CARD_H, body)


TAG_H = 42
TAG_TOP = 184.8  # 元の札（コスチューム「チームN」）と同じ高さに出す（回転の中心から絵の上端まで）


def team_tag(team: int, name: str) -> Costume:
    """チーム紹介と出題中に上に出る札。元の札（「チーム1」の絵）と同じ形で、チーム名を書く。"""
    size = _fitting_size(name, 18, 456 - 24)
    width = max(98.0, round(_text_width(name, size) + 24, 1))
    body = (
        f'<rect x="1.75" y="4.8" width="{width - 3.5}" height="35.37" fill="{TEAM_COLORS[team - 1]}" fill-opacity="0.75" '
        'stroke="#000000" stroke-opacity="0.2" stroke-width="3.5"/>'
        f'<text x="{width / 2}" y="{22.5 + size * 0.36:.1f}" text-anchor="middle" font-size="{size}" fill="#000000" {JP}>{escape(name)}</text>'
    )
    return Costume(f"チーム{team}", _svg(width, TAG_H, body), (width / 2, TAG_TOP))


def _digit(digit: int) -> Costume:
    body = (
        f'<g {DIGIT} font-size="48" text-anchor="middle">'
        f'<text x="20" y="49" fill="#ffffff" stroke="#000000" stroke-width="8" stroke-linejoin="round">{digit}</text>'
        f'<text x="20" y="49" fill="#ffffff" stroke="#ffffff" stroke-width="2">{digit}</text></g>'
    )
    return _costume(f"数{digit}", 40, 64, body)


def _rank(rank: int) -> Costume:
    body = (
        f'<rect x="1.5" y="1.5" width="61" height="21" rx="10.5" fill="{RANK_COLORS[rank - 1]}" stroke="#1a1a1a" stroke-opacity="0.35" stroke-width="1.5"/>'
        f'<text x="32" y="17.5" text-anchor="middle" font-size="14" fill="#1a1a1a" {JP}>{rank}位</text>'
    )
    return _costume(f"順位{rank}", 64, 24, body)


def _game_over() -> Costume:
    body = (
        '<rect x="1.5" y="1.5" width="85" height="21" rx="10.5" fill="#e5383b" stroke="#ffffff" stroke-opacity="0.6" stroke-width="1.5"/>'
        f'<text x="44" y="17" text-anchor="middle" font-size="12.5" letter-spacing="0.6" fill="#ffffff" {LATIN}>GAME OVER</text>'
    )
    return _costume("ゲームオーバー", 88, 24, body)


def _winner() -> Costume:
    body = (
        '<rect x="2" y="2" width="92" height="30" rx="15" fill="#ffd23f" stroke="#8a5a00" stroke-width="2.5"/>'
        # 王冠
        '<path d="M14,23 L11,11 L18,16 L23,8 L28,16 L35,11 L32,23 Z" fill="#8a5a00"/>'
        f'<text x="61" y="23.5" text-anchor="middle" font-size="18" letter-spacing="3" fill="#5c3b00" {JP}>優勝</text>'
    )
    return _costume("優勝", 96, 34, body)


def _gold_frame() -> Costume:
    body = f'<rect x="3" y="3" width="{CARD_W - 6}" height="{CARD_H - 6}" rx="10" fill="none" stroke="#ffd23f" stroke-width="6"/>'
    return _costume("金枠", CARD_W, CARD_H, body)


def costumes(team_names: dict[int, str] | None = None) -> list[Costume]:
    names = team_names or {}
    return [
        _background(),
        *(_card(team, names.get(team)) for team in range(1, 6)),
        *(_digit(d) for d in range(10)),
        *(_rank(rank) for rank in range(1, 6)),
        _game_over(),
        _winner(),
        _gold_frame(),
    ]


def assets(team_names: dict[int, str] | None = None) -> dict[str, bytes]:
    """この画面のために足す素材（sb3 の中でのファイル名 → 中身）。チームの札も含む。"""
    added = [*costumes(team_names), *(team_tag(team, name) for team, name in (team_names or {}).items())]
    return {c.md5ext: c.data for c in added}


# ---- スクリプト ------------------------------------------------------


def _look(name: Any) -> Block:
    return Block("looks_switchcostumeto", {"COSTUME": name})


def _go(x: Any, y: Any) -> Block:
    return Block("motion_gotoxy", {"X": x, "Y": y})


def _size(percent: Any) -> Block:
    return Block("looks_setsizeto", {"SIZE": percent})


def _ghost(value: int) -> Block:
    return Block("looks_seteffectto", {"VALUE": value}, {"EFFECT": "GHOST"})


def _front() -> Block:
    return Block("looks_gotofrontback", fields={"FRONT_BACK": "front"})


def _show() -> Block:
    return Block("looks_show")


def _in_card(dy: float) -> Block:
    """カードの中心から dy だけ上の高さ（カードの大きさに合わせて縮む）。"""
    return plus(CARD_Y, times(dy / 100, Var("倍率")))


def _quiet_others(project: dict[str, Any]) -> None:
    """結果発表の画面を出すとき、ほかのスプライトを止めて隠す（裏方PCとやり取りする「通信」は除く）。

    10/18 の版から、残りバルーンの数字は描くたびに最前面へ出るので、あとから出したこの画面より手前に来て、
    カードに重なっていた（順に進めたときだけ。開き直したときは数字を出さずに組み立てるので重ならない）。
    風船やカゴも背景より手前に透けていた。動きを止めないと、隠しても次のコマでまた出てくる。
    """
    for target in project["targets"]:
        if target.get("isStage") or target["name"] in (SPRITE, "通信"):
            continue
        Scripts(project, target["name"]).add(on_message(SHOW_MESSAGE), stop_other_scripts(), Block("looks_hide"))


def add_result_screen(project: dict[str, Any], team_names: dict[int, str] | None = None) -> None:
    """スプライト「結果発表」を足す。「結果_出す」を受けると、リスト「得点表」から画面を作る。"""
    add_sprite(project, SPRITE, costumes(team_names))
    _quiet_others(project)
    s = Scripts(project, SPRITE)
    # スプライトだけの変数。クローンは、作られたときの値を自分の分として持つ
    for name in ("役", "番", "桁目", "点", "倍率", "横", "数", "最大", "順位", "比"):
        s.add_variable(name)

    def score_of(index: Any) -> Block:
        return item(index, SCORES_LIST)

    # 番目のカードの横の位置。全体をまんなかに寄せる
    card_x = times(minus(Var("番"), divide(plus(Var("数"), 1), 2)), times(CARD_PITCH / 100, Var("倍率")))

    s.add(
        on_message(SHOW_MESSAGE),
        set_var("役", "背景"),
        _look("背景"),
        _go(0, 0),
        _size(100),
        Block("looks_cleargraphiceffects"),
        _front(),
        _show(),
        set_var("数", list_length(SCORES_LIST)),
        set_var("倍率", 100),
        when(gt(Var("数"), 4), set_var("倍率", 84)),  # 5チームは横に入りきらないので縮める
        # 優勝は、残りバルーンが最多のチーム（同数なら全チーム。全チームが0なら優勝なし）
        set_var("最大", 0),
        set_var("番", 0),
        repeat(
            Var("数"),
            change_var("番", 1),
            when(gt(score_of(Var("番")), Var("最大")), set_var("最大", score_of(Var("番")))),
        ),
        wait(0.6),
        set_var("番", 0),
        repeat(
            Var("数"),
            change_var("番", 1),
            set_var("横", card_x),
            set_var("点", score_of(Var("番"))),
            set_var("役", "カード"),
            create_clone(),
            set_var("桁目", 0),
            repeat(length(Var("点")), change_var("桁目", 1), set_var("役", "数字"), create_clone()),
            when_else(
                lt(Var("点"), 1),
                [set_var("役", "ゲームオーバー"), create_clone()],
                [
                    # 順位は「自分より多いチームの数 + 1」。同数は同じ順位
                    set_var("順位", 1),
                    set_var("比", 0),
                    repeat(
                        Var("数"),
                        change_var("比", 1),
                        when(gt(score_of(Var("比")), Var("点")), change_var("順位", 1)),
                    ),
                    set_var("役", "順位"),
                    create_clone(),
                ],
            ),
            wait(0.4),
        ),
        wait(0.5),
        set_var("番", 0),
        repeat(
            Var("数"),
            change_var("番", 1),
            when(
                both(gt(Var("最大"), 0), eq(score_of(Var("番")), Var("最大"))),
                set_var("横", card_x),
                set_var("役", "金枠"),
                create_clone(),
                set_var("役", "優勝"),
                create_clone(),
            ),
        ),
        set_var("役", "背景"),
    )

    digit_x = plus(
        Var("横"),
        times(
            minus(Var("桁目"), divide(plus(length(Var("点")), 1), 2)),
            times(0.3, Block("looks_size")),  # 数字1つの幅は、100% のとき30
        ),
    )
    s.add(
        on_clone_start(),
        _size(Var("倍率")),
        _front(),
        when(
            eq(Var("役"), "カード"),
            _look(join("カード", Var("番"))),
            _go(Var("横"), CARD_Y - 20),
            _ghost(100),
            _show(),
            repeat(
                5,
                Block("motion_changeyby", {"DY": 4}),
                Block("looks_changeeffectby", {"CHANGE": -20}, {"EFFECT": "GHOST"}),
            ),
        ),
        when(
            eq(Var("役"), "数字"),
            _look(join("数", letter(Var("桁目"), Var("点")))),
            when(gt(length(Var("点")), 2), _size(times(0.8, Var("倍率")))),  # 3桁（100）は縮める
            _go(digit_x, _in_card(-12)),
            wait(0.2),
            _show(),
        ),
        when(eq(Var("役"), "順位"), _look(join("順位", Var("順位"))), _go(Var("横"), _in_card(-76)), wait(0.3), _show()),
        when(eq(Var("役"), "ゲームオーバー"), _look("ゲームオーバー"), _go(Var("横"), _in_card(-76)), wait(0.3), _show()),
        when(eq(Var("役"), "金枠"), _look("金枠"), _go(Var("横"), CARD_Y), _show()),
        when(
            eq(Var("役"), "優勝"),
            _look("優勝"),
            _go(Var("横"), _in_card(124)),
            _size(10),
            _show(),
            repeat(6, Block("looks_changesizeby", {"CHANGE": divide(Var("倍率"), 6)})),
            _size(Var("倍率")),  # 小さすぎる大きさは Scratch が切り上げるので、最後に合わせ直す
        ),
    )

    # 画面を組み立て直すとき・次のゲームを始めるときに消す
    s.add(on_message("作り直し_止める"), stop_other_scripts())
    s.add(on_message("初期化"), Block("looks_hide"), Block("control_delete_this_clone"))

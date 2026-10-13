"""観客用ページ（スマホから参加する画面）の生成。

観客の画面は cloud 変数を**読むだけ**で、ステージ画面とまったく同じ
P2S_* を受け取る。そのため進行は自動的に同期する
（docs/architecture.md「進行の同期」）。回答は端末内で採点し、
サーバへは成績（誤差の合計）だけを送る。

生成物は cloud-server の `public/` に置く。cloud-server は public/ を
そのまま静的配信するので、**サーバを増やさずに観客ページを配れる**。

問題を差し替えたら必ず再生成すること（問題文がページに埋め込まれているため）。
書き出しは司会者画面と一緒に publish.py で行う:

    uv run python -m suzuleague.publish --room-id 1364239598
"""

from __future__ import annotations

import json
from pathlib import Path

from .questions import QuestionSet

TEMPLATE_PATH = Path(__file__).parent / "audience_template.html"

# テンプレート側のプレースホルダ。JSリテラルとして置換する
PLACEHOLDER_QUESTIONS = "__QUESTIONS__"
PLACEHOLDER_ROOM = "__ROOM__"


def build(room_id: str, question_set: QuestionSet | None = None) -> str:
    """観客用ページのHTMLを生成する。

    正解値は埋め込まない。先に見えてしまうため、
    正解は発表のタイミングで P2S_CORRECT として届くのを待つ。
    """
    qs = question_set or QuestionSet()
    html = TEMPLATE_PATH.read_text(encoding="utf-8")

    for placeholder in (PLACEHOLDER_QUESTIONS, PLACEHOLDER_ROOM):
        if placeholder not in html:
            raise ValueError(f"テンプレートに {placeholder} がありません")

    html = html.replace(PLACEHOLDER_QUESTIONS, qs.export_audience_json())
    html = html.replace(PLACEHOLDER_ROOM, json.dumps(room_id)[1:-1])
    return html

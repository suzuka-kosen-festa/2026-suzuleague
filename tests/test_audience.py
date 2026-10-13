"""観客用ページ生成のテスト（ネットワーク不要）。

観客の画面に正解が漏れると出題の意味がなくなるので、
そこを重点的に確認する。
"""

from __future__ import annotations

import json
import re

import pytest

from suzuleague import protocol
from suzuleague.audience import build
from suzuleague.models import Question
from suzuleague.questions import QuestionSet


@pytest.fixture
def html() -> str:
    return build("suzuleague-test")


class TestBuild:
    def test_placeholders_are_replaced(self, html):
        assert "__QUESTIONS__" not in html
        assert "__ROOM__" not in html
        assert "__REVEAL_DELAY_MS__" not in html

    def test_room_id_is_embedded(self, html):
        assert '"suzuleague-test"' in html

    def test_all_question_texts_are_embedded(self, html):
        for q in QuestionSet().questions:
            assert q.text in html

    def test_correct_answers_are_not_embedded(self):
        """正解値がページに含まれてはいけない（先に見えてしまう）。"""
        marker = "正解が漏れていないか確認するための問題文"
        questions = [
            Question(i, f"{marker}{i}", 73, "テスト") for i in range(1, 21)
        ]
        html = build("room", QuestionSet(questions))

        # 埋め込まれた問題テーブルを取り出し、値が問題文だけであることを確認する
        m = re.search(r"var QUESTIONS = (\{.*?\n\});", html, re.DOTALL)
        assert m, "問題テーブルが見つからない"
        table = json.loads(m.group(1))
        assert len(table) == 20
        for key, value in table.items():
            assert value == f"{marker}{key}"
            assert "73" not in value

    def test_audience_never_writes_s2p(self, html):
        """観客が S2P_* を書くと挑戦者の回答として採点されてしまう。"""
        # 送信しているのは handshake のみ
        sends = re.findall(r"ws\.send\((.*?)\);", html, re.DOTALL)
        assert len(sends) == 1
        assert "handshake" in sends[0]
        assert "S2P" not in sends[0]

    def test_reveal_waits_for_scratch_animation(self, html):
        """ステージ画面より先に正解を出さない（ゲージの演出は実測で最長13.7秒）。"""
        m = re.search(r"var REVEAL_DELAY_MS = (\d+);", html)
        assert m, "正解発表の待ち時間が見つからない"
        assert int(m.group(1)) == protocol.REVEAL_SECONDS * 1000  # 司会の「次へ」を止める長さと同じ
        assert int(m.group(1)) >= 13_700

    def test_stage_answer_comes_from_the_cloud_variable(self, html):
        """ステージの回答は P2S_ANSWER から出す（演出のあいだ、公開用の状態には結果が入らないため）。"""
        assert 'num("P2S_ANSWER")' in html
        assert html.count('fetch("api/player/state"') == 1  # 全体結果の表示だけ

    def test_no_external_resources(self, html):
        """会場ネットワークに依存しないよう外部読み込みを持たない。"""
        assert not re.search(r'src="https?://', html)
        assert not re.search(r'href="https?://[^"]*\.css', html)


class TestQuestionSetExport:
    def test_audience_json_has_no_answers(self):
        data = json.loads(QuestionSet().export_audience_json())
        assert len(data) == 20
        for qid, text in data.items():
            assert isinstance(text, str)
            assert int(qid) >= 1

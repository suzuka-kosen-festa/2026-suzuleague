"""問題セット管理（コード内管理）。

本番の問題は企画側が実施したアンケート2種の集計結果から作成している
（アンケート1: 485件 / アンケート2: 423件、いずれも2026-06-26実施）。

問題文はcloud変数（数値のみ）で送れないため、ScratchへはIDのみ送信する
「ID参照方式」を採用。Scratch側には表示用テキストのリストを持ってもらい、
その貼り付け用テキストを export_scratch_list() で生成できる。
"""

from __future__ import annotations

from .models import Question

ROUNDS_PER_TEAM = 5
TEAM_COUNT = 4

SURVEY_1 = "鈴鹿高専生485人アンケート"
SURVEY_2 = "鈴鹿高専生423人アンケート"

# 本番問題。IDは通し番号で、チームtのラウンドr(1始まり)の問題ID = (t-1)*5 + r
#
# 企画側が決定した問題表（Googleスプレッドシート「スズリーグ　問題」、2026-10）を
# そのまま転記している。チームへの割り当て・出題順も問題表どおり。
# 問題表から変えたのは次の点だけ:
#   - 誤字の修正（「同行会」→「同好会」、前校長の名前はアンケートの設問どおり「竹茂」）
#   - コンソメ味の正解を37→38に修正（アンケート1の原データが186/487人=38.2%のため）
#
# 正解値は集計結果の四捨五入。差し替えるときは docs/development.md の
# 「リリースチェックリスト」に従い、Scratch側のリストと観客ページも必ず同時に更新すること。
PRODUCTION_QUESTIONS: list[Question] = [
    # チーム1
    Question(1, "三重県に住んでいる人は何パーセント？", 93, SURVEY_1),
    Question(2, "テスト期間中、少なくとも3本はエナジードリンクを飲むと答えた人は何パーセント？", 19, SURVEY_2),
    Question(3, "通学に1時間以上かかると答えた人は何パーセント？", 40, SURVEY_2),
    Question(4, "高専に入学して赤点を取ったことはあるという人は何パーセント？", 70, SURVEY_1),
    Question(5, "中学校で内申点45をとったことのある人は何パーセント？", 27, SURVEY_1),
    # チーム2
    Question(6, "部活動または同好会に所属している人は何パーセント？", 93, SURVEY_1),
    Question(7, "前校長竹茂求先生のインスタグラムをフォローしている人は何パーセント？", 31, SURVEY_1),
    Question(8, "自分のパソコンを持っていると答えた人は何パーセント？", 55, SURVEY_1),
    Question(9, "テスト期間は日付を跨ぐ前に寝ているという人は何パーセント？", 28, SURVEY_2),
    Question(
        10,
        "体操服の色を選べるとしたときに，赤，青，緑，紫，黄色の中で赤を選んだ人は何パーセント？",
        17,
        SURVEY_1,
    ),
    # チーム3
    Question(11, "鈴鹿高専に第1志望で入学していないという人は何パーセント？", 8, SURVEY_1),
    Question(12, "BeRealをダウンロードしてると答えた人は何パーセント？", 37, SURVEY_2),
    Question(13, "告白したこと・もしくはされたことがあると答えた人は何パーセント？", 68, SURVEY_1),
    Question(14, "高専の定期テストで45点以下を取ったことがあると答えた人は何パーセント？", 43, SURVEY_2),
    Question(15, "中学時代に数学が一番得意だったと答えた人は何パーセント？", 39, SURVEY_2),
    # チーム4
    Question(16, "今年度の前期に紙媒体を使って勉強したことがある人は何パーセント？", 85, SURVEY_1),
    Question(17, "3DSで妖怪ウォッチシリーズをプレイしたことがあると答えた人は何パーセント？", 54, SURVEY_2),
    Question(18, "ポテトチップスの味の中で、コンソメ味が１番好きと答えた人は何パーセント？", 38, SURVEY_1),
    Question(19, "身長が175cm以上あると答えた人は何パーセント？", 18, SURVEY_2),
    Question(20, "将来、自分の子供にも高専に入学してほしい人は何パーセント？", 31, SURVEY_2),
]

# 予備。問題を差し替えたくなったときの候補（集計済み・そのまま使える）。
# 問題表で採用されなかった設問。ID は 101 以降にして本番の通し番号と衝突させない。
SPARE_QUESTIONS: list[Question] = [
    Question(101, "月子チェックに行ったことがある人は何パーセント？", 49, SURVEY_2),
    Question(102, "購買のクッキーシューを食べたことがある人は何パーセント？", 62, SURVEY_1),
    Question(103, "卒業後の進路に進学を希望している人は何パーセント？", 53, SURVEY_1),
    Question(104, "体操服の色を選べるなら青を選ぶ人は何パーセント？", 56, SURVEY_1),
    Question(105, "アルバイトをしたことがある人は何パーセント？", 55, SURVEY_1),
    Question(106, "Nintendo Switch2を買った人は何パーセント？", 24, SURVEY_2),
    Question(107, "中学のテストでランキング10位以内に入ったことがある人は何パーセント？", 70, SURVEY_2),
    Question(108, "ゲームをするのが好きな人は何パーセント？", 87, SURVEY_2),
    Question(109, "過去と未来なら、過去に行きたい人は何パーセント？", 64, SURVEY_2),
    Question(110, "SNSのサブスクリプションを1つ以上登録している人は何パーセント？", 63, SURVEY_2),
]

# 旧名の互換用エイリアス（プロトタイプ期のサンプル問題は本番データに差し替え済み）
SAMPLE_QUESTIONS = PRODUCTION_QUESTIONS


class QuestionSet:
    """チーム×ラウンドへの問題割り当てを管理する。"""

    def __init__(self, questions: list[Question] | None = None) -> None:
        self.questions = questions if questions is not None else PRODUCTION_QUESTIONS
        need = TEAM_COUNT * ROUNDS_PER_TEAM
        if len(self.questions) < need:
            raise ValueError(f"問題数が不足: {len(self.questions)} < {need}")
        self._by_id = {q.id: q for q in self.questions}
        if len(self._by_id) != len(self.questions):
            raise ValueError("問題IDが重複しています")

    def by_id(self, question_id: int) -> Question:
        return self._by_id[question_id]

    def for_team(self, team_no: int) -> list[Question]:
        """チーム番号(1始まり)に割り当てられた5問を返す。"""
        if not 1 <= team_no <= TEAM_COUNT:
            raise ValueError(f"チーム番号は1-{TEAM_COUNT}: {team_no}")
        start = (team_no - 1) * ROUNDS_PER_TEAM
        return self.questions[start : start + ROUNDS_PER_TEAM]

    def export_scratch_list(self) -> str:
        """Scratchのリストに貼り付けるためのテキストを生成する。

        行番号 = 問題ID になるようにID順で1行1問。
        Scratchエディタでリストを右クリック→「読み込み」で取り込める。
        """
        lines = []
        for i in range(1, max(self._by_id) + 1):
            q = self._by_id.get(i)
            lines.append(q.text if q else "")
        return "\n".join(lines) + "\n"

    def export_audience_json(self) -> str:
        """観客用ページに埋め込む問題文のJSONを生成する。

        観客の画面は正解を先に知ってはいけないので、**問題文だけ**を渡す。
        正解は正解発表のタイミングで cloud 変数 P2S_CORRECT から届く。
        """
        import json

        table = {str(q.id): q.text for q in sorted(self.questions, key=lambda q: q.id)}
        return json.dumps(table, ensure_ascii=False, indent=2)


def main() -> None:
    """問題リストを外部向けの形式で書き出す。

    使い方:
      uv run python -m suzuleague.questions > questions.txt        # Scratch貼り付け用
      uv run python -m suzuleague.questions --audience-json        # 観客ページ用
    """
    import argparse

    parser = argparse.ArgumentParser(description="問題リストの書き出し")
    parser.add_argument(
        "--audience-json",
        action="store_true",
        help="観客用ページに埋め込むJSONを出力する（既定はScratch貼り付け用テキスト）",
    )
    args = parser.parse_args()

    qs = QuestionSet()
    if args.audience_json:
        print(qs.export_audience_json())
    else:
        print(qs.export_scratch_list(), end="")


if __name__ == "__main__":
    main()

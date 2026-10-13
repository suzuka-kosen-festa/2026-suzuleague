"""プロトコルのエンコード/デコードのテスト。"""

import pytest

from suzuleague import protocol
from suzuleague.engine import Snapshot, State


class TestEncodeSnapshot:
    def test_encode_all_fields(self):
        snap = Snapshot(
            state=State.REVEAL,
            team_no=2,
            round_no=3,
            question_id=8,
            correct=67,
            balloons=54,
        )
        encoded = protocol.encode_snapshot(snap, seq=12)
        assert encoded == {
            "P2S_STATE": 4,
            "P2S_TEAM": 2,
            "P2S_ROUND": 3,
            "P2S_QID": 8,
            "P2S_CORRECT": 67,
            "P2S_BALLOONS": 54,
            "P2S_ANSWER": -1,
            "P2S_SCORES": 1,
            "P2S_FAILED_ROUND": 0,
            "P2S_SEQ": 12,
        }

    def test_seq_is_last_key(self):
        """SEQは他の変数の後に送信される必要がある（Scratch側の検知順序）。"""
        snap = Snapshot(State.IDLE, 0, 0, 0, -1, 0)
        keys = list(protocol.encode_snapshot(snap, seq=1))
        assert keys[-1] == protocol.VAR_P2S_SEQ

    def test_values_are_all_int(self):
        """TurboWarp cloudは数値のみのため、全て整数で送る。"""
        snap = Snapshot(State.ANSWERING, 1, 1, 1, -1, 100)
        for value in protocol.encode_snapshot(snap, seq=1).values():
            assert isinstance(value, int)


class TestEncodeScores:
    def test_three_digits_per_team_after_a_leading_one(self):
        assert protocol.encode_scores((38, 0, -1, -1)) == 1038000999999

    def test_full_and_single_team(self):
        assert protocol.encode_scores((100,)) == 1100
        assert protocol.encode_scores(()) == 1

    def test_snapshot_carries_only_finished_teams(self):
        from suzuleague.engine import GameEngine

        engine = GameEngine()
        engine.advance()
        engine.advance()  # チーム1の1問目
        for _ in range(5):
            engine.advance()
            engine.submit_answer(engine.current_question.correct)  # ぴったり → 100のまま
            engine.advance()
            engine.advance()
            assert engine.snapshot().scores == ((100, -1, -1, -1) if engine.current_team.finished_rounds == 5 else (-1,) * 4)
            engine.advance()
        assert engine.state is State.TEAM_RESULT
        assert protocol.encode_snapshot(engine.snapshot(), 1)["P2S_SCORES"] == 1100999999999


class TestFailedRound:
    """開き直したステージ画面が、その問題で GAME OVER になったかを知るための値。"""

    def test_round_of_game_over_then_kept_through_exhibition(self):
        from suzuleague.engine import GameEngine

        engine = GameEngine(initial_balloons=10)
        engine.advance()
        engine.advance()  # チーム1の1問目
        seen = []
        for _ in range(5):
            engine.advance()
            question = engine.current_question
            # 2問目で50以上ずれてゲームオーバー、そのあとはエキシビション（採点なし）
            far = 0 if question.correct >= 50 else 100
            engine.submit_answer(far if engine.snapshot().round_no == 2 else question.correct)
            engine.advance()
            seen.append(protocol.encode_snapshot(engine.snapshot(), 1)["P2S_FAILED_ROUND"])
            engine.advance()
            engine.advance()
        assert engine.current_team.balloons == 0
        assert seen == [0, 2, 2, 2, 2]
        engine.advance()  # 次のチーム
        assert engine.snapshot().failed_round == 0


class TestParsePercent:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("45", 45),
            (45, 45),
            ("45.0", 45),  # cloudからは小数表記で届くことがある
            ("0", 0),
            ("100", 100),
        ],
    )
    def test_valid_values(self, raw, expected):
        assert protocol.parse_percent(raw) == expected

    @pytest.mark.parametrize("raw", ["101", "-1", "45.5", "abc", "", None, "1e10"])
    def test_invalid_values(self, raw):
        with pytest.raises(protocol.ProtocolError):
            protocol.parse_percent(raw)

"""スズリーグのドメインモデル。

通信やUIに依存しない純粋なデータクラスのみを置く。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Question:
    """クイズ1問。正解はパーセント値(0-100)。"""

    id: int
    text: str
    correct: int  # 正解パーセント (0-100)
    source: str = ""  # アンケートの出典（例: "3年電子情報工学科"）

    def __post_init__(self) -> None:
        if not 0 <= self.correct <= 100:
            raise ValueError(f"正解パーセントは0-100: {self.correct}")


@dataclass(frozen=True)
class RoundResult:
    """1ラウンド（1問）の結果。"""

    round_no: int  # 1-5
    question_id: int
    answer: int  # 回答されたパーセント
    correct: int  # 正解パーセント
    damage: int  # 割れたバルーン数
    balloons_after: int  # このラウンド終了時点のバルーン残数
    exhibition: bool = False  # エキシビションラウンドか

    @property
    def is_perfect(self) -> bool:
        return self.answer == self.correct


@dataclass
class Team:
    """挑戦チーム。持ち点=バルーン100からスタート。"""

    number: int  # チーム番号 (1-4)
    name: str
    members: list[str] = field(default_factory=list)
    balloons: int = 100
    results: list[RoundResult] = field(default_factory=list)

    @property
    def is_failed(self) -> bool:
        """バルーンが尽きた（ゲームオーバー）か。"""
        return self.balloons <= 0

    @property
    def finished_rounds(self) -> int:
        return len(self.results)

    @property
    def failed_round(self) -> int:
        """ゲームオーバーになった問題（1-5）。なっていなければ0。"""
        for r in self.results:
            if not r.exhibition and r.balloons_after <= 0:
                return r.round_no
        return 0

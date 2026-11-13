"""部門対抗モードの司会者画面・回答ページ・大画面に渡す状態と、届いた操作の実行。

高専祭本番の host.py と同じ中継（HostRelay）に載せる。状態の形と操作の種類が違うので、
host.py に分岐を足さずにこちらへ分けた（docs/furikaeri/design.md「既存のコードとの分け方」）。

- build_host_state: 司会者画面に出す状態。正解と締め切り前の予想も入る（Render は司会の合言葉付きでしか返さない）
- build_player_state: 回答ページと大画面に出す公開用の状態。誰でも読めるので、
  予想は予想オープンから、正解は正解発表から入れる
- execute_command: 司会者画面と回答ページから届いた操作を1つ実行する

回答ページに合言葉はない。部門はボタンで選び、回答には部門番号を付けて送る（2026-11-13 決定。
参加者は全員どこかの部門の人なので、ほかの部門として答えるいたずらは起きないとみなした）。
"""

from __future__ import annotations

from typing import Any

from .battle import BattleEngine, BattleTeam
from .controller import GameController
from .engine import GameError, State
from .labels import STATE_LABELS

# 予想を公開してよい状態（予想オープンから最終順位まで）
OPEN_STATES = (State.OPEN, State.REVEAL, State.ROUND_RESULT, State.FINISHED)
# 正解を公開してよい状態
REVEALED_STATES = (State.REVEAL, State.ROUND_RESULT, State.FINISHED)

# 正解発表の数え下げ演出（board.html の REVEAL_COUNT_MS と合わせる）のあいだ「次へ」を止める秒数
BATTLE_REVEAL_HOLD = 4.0


# ---- 画面に出す状態 ------------------------------------------------


def next_action_label(engine: BattleEngine) -> str | None:
    """「次へ」を押すと何が起きるか。ボタンに書いて押し間違いを防ぐ。"""
    state = engine.state
    if state is State.IDLE:
        return "1問目を出題する"
    if state is State.QUESTION:
        return "回答受付を始める"
    if state is State.ANSWERING:
        return "予想をオープンする"
    if state is State.OPEN:
        return "正解を発表する"
    if state is State.REVEAL:
        return "途中順位を出す"
    if state is State.ROUND_RESULT:
        if engine.round_no >= engine.rounds:
            return "最終順位を出す"
        return f"{engine.round_no + 1}問目を出題する"
    return None


def _question(engine: BattleEngine, *, with_answer: bool) -> dict[str, Any] | None:
    question = engine.current_question
    if question is None:
        return None
    info: dict[str, Any] = {"id": question.id, "text": question.text}
    if with_answer:
        info["source"] = question.source
        info["correct"] = question.correct
    return info


def _ranks(engine: BattleEngine) -> dict[int, int]:
    return {s.team.number: s.rank for s in engine.standings()}


def _team_public(engine: BattleEngine, team: BattleTeam, ranks: dict[int, int]) -> dict[str, Any]:
    opened = engine.state in OPEN_STATES
    result = engine.last_result(team)
    entry: dict[str, Any] = {
        "number": team.number,
        "name": team.name,
        "balloons": team.balloons,
        "eliminated": team.eliminated,
        "error_total": team.error_total,
        "rank": ranks.get(team.number),
        "answered": team.number in engine.answers,
        "answer": engine.answers.get(team.number) if opened else None,
        "error": None,
        "damage": None,
    }
    if result is not None:
        entry.update(answer=result.answer, error=result.error, damage=result.damage)
    return entry


def build_player_state(engine: BattleEngine) -> dict[str, Any]:
    """回答ページと大画面に出す状態。誰でも読めるので、予想と正解を先に入れない。"""
    revealed = engine.state in REVEALED_STATES
    question = engine.current_question
    ranks = _ranks(engine)
    return {
        "mode": "battle",
        "state": int(engine.state),
        "state_label": STATE_LABELS[engine.state],
        "rehearsal": engine.is_rehearsal,
        "round": engine.round_no,
        "rounds": engine.rounds,
        "question": _question(engine, with_answer=False),
        "correct": question.correct if (question and revealed) else None,
        # cloud-server はこの名前と question.id を見て回答を受け付ける（高専祭本番と同じ名前）
        "accepting_answer": engine.state is State.ANSWERING,
        "teams": [_team_public(engine, t, ranks) for t in engine.active_teams],
        "finished": engine.state is State.FINISHED,
        "winners": [{"number": t.number, "name": t.name} for t in engine.winners()],
    }


def build_host_state(
    engine: BattleEngine,
    last_command: dict[str, Any] | None = None,
    hold_remaining: float = 0.0,
) -> dict[str, Any]:
    """司会者画面に出す状態。正解と締め切り前の予想も入る。"""
    ranks = _ranks(engine)
    teams = []
    for team in engine.teams:
        entry = _team_public(engine, team, ranks) if team.active else {
            "number": team.number,
            "name": team.name,
            "balloons": team.balloons,
            "eliminated": False,
            "rank": None,
            "answered": False,
        }
        entry["active"] = team.active
        entry["answer"] = (
            engine.answers.get(team.number) if engine.state is State.ANSWERING else entry.get("answer")
        )
        teams.append(entry)
    missing = [t.name for t in engine.missing_teams()] if engine.state is State.ANSWERING else []
    return {
        "mode": "battle",
        "state": int(engine.state),
        "state_label": STATE_LABELS[engine.state],
        "next_action": next_action_label(engine),
        "rehearsal": engine.is_rehearsal,
        "round": engine.round_no,
        "rounds": engine.rounds,
        "question": _question(engine, with_answer=True),
        "accepting_answer": engine.state is State.ANSWERING,
        "missing": missing,
        "teams": teams,
        "finished": engine.state is State.FINISHED,
        "winners": [{"number": t.number, "name": t.name} for t in engine.winners()],
        "last_command": last_command,
        "hold": int(-(-hold_remaining // 1)),  # 切り上げ
        # 部門対抗モードに合言葉はない。cloud-server は player.mode を見て、部門番号で回答を受け付ける
        "player_code": None,
        "player": build_player_state(engine),
    }


# ---- 操作の実行 ----------------------------------------------------


def _int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GameError(f"{what}の形式が正しくありません")
    return value


def _check_expect(engine: BattleEngine, command: dict[str, Any]) -> None:
    expect = command.get("expect_state")
    if expect is not None and _int(expect, "操作") != int(engine.state):
        raise GameError("画面の表示が古いため操作を取り消しました（もう進んでいます）")


def execute_command(controller: GameController, command: dict[str, Any]) -> dict[str, Any]:
    """司会者画面・回答ページから届いた操作を1つ実行し、結果を返す（例外は投げない）。"""
    engine: BattleEngine = controller.engine  # type: ignore[assignment]
    seq = command.get("seq")
    kind = command.get("type")
    from_player = command.get("source") == "player"
    try:
        if from_player and kind != "answer":
            raise GameError("部門からは回答しか受け付けません")
        if kind == "next":
            expect = command.get("expect_state")
            state = controller.advance(expect_state=None if expect is None else _int(expect, "操作"))
            message = f"「{STATE_LABELS[state]}」に進みました"
        elif kind == "close":

            def close() -> State:
                _check_expect(engine, command)
                return engine.close()

            controller.apply(close)
            message = "未回答の部門を残したまま締め切りました（予想50とみなします）"
        elif kind == "set_active":
            team_no = _int(command.get("team"), "部門")
            active = command.get("active")
            if not isinstance(active, bool):
                raise GameError("参加の切り替えの形式が正しくありません")
            controller.apply(lambda: engine.set_active(team_no, active))
            name = engine.team(team_no).name
            message = f"{name}を{'参加' if active else '不参加'}にしました"
        elif kind == "answer":
            value = _int(command.get("value"), "回答")

            def answer() -> str:
                team = engine.team(_int(command.get("team"), "部門"))
                if from_player:
                    question = engine.current_question
                    if question is None or command.get("question_id") != question.id:
                        raise GameError("前の問題への回答が遅れて届いたため、受け付けませんでした")
                engine.submit_answer(team.number, value)
                return team.name

            name = controller.apply(answer)
            message = (
                f"{name}から回答 {value}% が届きました"
                if from_player
                else f"{name}の回答 {value}% を代理で入力しました"
            )
        else:
            raise GameError(f"不明な操作です: {kind!r}")
    except GameError as e:
        return {"seq": seq, "ok": False, "message": str(e)}
    return {"seq": seq, "ok": True, "message": message}

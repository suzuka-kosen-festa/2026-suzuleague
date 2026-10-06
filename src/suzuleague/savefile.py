"""進行の状態をファイルに保存し、裏方PCを起動し直したときに途中から再開する（#51）。

進行の状態（今何問目か・各チームのバルーン・出演者の合言葉）は裏方PCのメモリにしかない。
PCのスリープや「終了する」の押し間違いで最初に戻らないよう、操作のたびに書き出す。

    uv run suzuleague --save-file launcher/run/game-demo.json            # 最初から（前回分は .prev に残す）
    uv run suzuleague --save-file launcher/run/game-demo.json --resume   # 続きから
    uv run python -m suzuleague.savefile --describe launcher/run/game-demo.json --teams docs/demo/teams-demo.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .engine import GameEngine, State
from .host import TeamCodes
from .labels import STATE_LABELS
from .teams import resolve_teams

SAVE_VERSION = 1


def save(path: Path, engine: GameEngine, codes: TeamCodes) -> None:
    """今の進行を書き出す。書きかけのファイルが残らないよう、別名で書いてから置き換える。"""
    data = {
        "version": SAVE_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "game": engine.export_state(),
        "player_codes": codes.export(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"保存ファイルを読めません（{e}）") from e
    if not isinstance(data, dict) or data.get("version") != SAVE_VERSION:
        raise ValueError("保存ファイルの形式が違います")
    return data


def restore(data: dict[str, Any], engine: GameEngine, codes: TeamCodes) -> None:
    engine.restore_state(data["game"])
    codes.restore(data["player_codes"])


def check(data: dict[str, Any], engine: GameEngine) -> bool:
    """保存が今のチーム構成・問題で読めるか確かめ、続きから始める意味があるかを返す。

    本物のエンジンには触らない（読めなかったときに中途半端な状態を残さない）。
    """
    probe = GameEngine(teams=copy.deepcopy(engine.teams), question_set=engine.question_set)
    restore(data, probe, TeamCodes([t.number for t in probe.teams]))
    return is_resumable(probe)


def is_resumable(engine: GameEngine) -> bool:
    """続きから始める意味があるか。始まる前と終わった後は最初からでよい。"""
    return engine.state not in (State.IDLE, State.FINISHED)


def describe(engine: GameEngine, saved_at: str) -> str:
    """続きの中身を1行で（アプリのダイアログに出す）。"""
    team = engine.current_team
    assert team is not None
    round_no = engine.snapshot().round_no
    where = f"{round_no}問目・" if round_no else ""
    try:
        when = datetime.fromisoformat(saved_at).strftime("%m/%d %H:%M")
    except ValueError:
        when = saved_at
    return (
        f"チーム{team.number} {team.name}・{where}{STATE_LABELS[engine.state]}"
        f"（残りバルーン{team.balloons}。{when} に保存）"
    )


def back_up(path: Path) -> Path | None:
    """最初から始めるとき、前回の保存を .prev に移す（押し間違えても戻せるように）。"""
    if not path.exists():
        return None
    prev = path.with_name(path.stem + ".prev" + path.suffix)
    os.replace(path, prev)
    return prev


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="保存された進行の中身を確かめる")
    parser.add_argument("--describe", type=Path, required=True, help="保存ファイル")
    parser.add_argument("--teams", default=None, help="チーム構成JSON（起動時と同じもの）")
    args = parser.parse_args(argv)

    # 出力は launcher/app.sh が読む: none / invalid:<理由> / 続きの説明
    if not args.describe.exists():
        print("none")
        return 0
    try:
        engine = GameEngine(teams=resolve_teams(args.teams))
        codes = TeamCodes([t.number for t in engine.teams])
        data = load(args.describe)
        restore(data, engine, codes)
    except ValueError as e:
        print(f"invalid:{e}")
        return 0
    print(describe(engine, str(data.get("saved_at", ""))) if is_resumable(engine) else "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())

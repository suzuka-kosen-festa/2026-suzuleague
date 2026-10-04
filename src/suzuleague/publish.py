"""スマホ向けの画面を cloud-server に書き出す・本番と照合する。

画面は Render 上の cloud-server が `public/` から配信している。元ファイルは
このリポジトリにあるので、手でコピーすると忘れたときに食い違う
（とくに観客ページは問題文を埋め込んでいるので、問題を差し替えたら作り直しが要る）。
そこで書き出しと照合をこのコマンドにまとめる。

    # 書き出し（隣に cloud-server がある前提）。差分を確認して cloud-server でコミットする
    uv run python -m suzuleague.publish

    # 本番で配信中の画面が、手元から作ったものと一致するか確かめる
    uv run python -m suzuleague.publish --check

cloud-server は master に入ると Render が自動でデプロイする（約90秒）。
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from . import audience
from .cloud import DEFAULT_PROJECT_ID, resolve_project_id
from .host import HOST_PAGE_PATH

DEFAULT_SERVER_DIR = Path(__file__).resolve().parents[2].parent / "cloud-server"
PRODUCTION_URL = "https://suzuleague-cloud.onrender.com"


def build_pages(room_id: str) -> dict[str, str]:
    """配信する画面一式。キーは cloud-server の public/ からのパス。"""
    return {
        "suzuleague.html": audience.build(room_id),
        "host.html": HOST_PAGE_PATH.read_text(encoding="utf-8"),
    }


def write_pages(pages: dict[str, str], public_dir: Path) -> list[str]:
    """public/ に書き出し、中身が変わったファイル名を返す。"""
    if not public_dir.is_dir():
        raise FileNotFoundError(f"{public_dir} がありません（cloud-server の場所を --server-dir で指定）")
    changed = []
    for name, content in pages.items():
        path = public_dir / name
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.write_text(content, encoding="utf-8")
        changed.append(name)
    return changed


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as res:  # Render が寝ていると起動に数十秒かかる
        return res.read().decode("utf-8")


def check_pages(
    pages: dict[str, str], base_url: str, fetch: Callable[[str], str] = _fetch
) -> list[str]:
    """本番で配信中の画面と照合し、食い違いの説明を返す（空なら一致）。"""
    problems = []
    for name, content in pages.items():
        url = f"{base_url.rstrip('/')}/{name}"
        try:
            live = fetch(url)
        except (urllib.error.URLError, OSError) as e:
            problems.append(f"{name}: 取得できません（{e}）")
            continue
        if live != content:
            problems.append(f"{name}: 本番の中身が手元から作ったものと違います")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="スマホ向けの画面を cloud-server に書き出す")
    parser.add_argument(
        "--room-id",
        default=None,
        help="観客ページの接続先ルームID（省略時は SUZULEAGUE_PROJECT_ID）",
    )
    parser.add_argument(
        "--server-dir",
        type=Path,
        default=DEFAULT_SERVER_DIR,
        help=f"cloud-server のクローン先（既定: {DEFAULT_SERVER_DIR}）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="書き出さずに、本番で配信中の画面と照合する",
    )
    parser.add_argument("--url", default=PRODUCTION_URL, help="--check で照合する先")
    parser.add_argument(
        "--allow-dev-room",
        action="store_true",
        help=f"開発用ルーム（{DEFAULT_PROJECT_ID}）での書き出しを許可する",
    )
    args = parser.parse_args(argv)

    room_id = resolve_project_id(args.room_id)
    if room_id == DEFAULT_PROJECT_ID and not args.allow_dev_room:
        # 開発用ルームのまま本番に出すと、観客のスマホに何も表示されない
        parser.error(
            f"接続先が開発用ルーム（{DEFAULT_PROJECT_ID}）です。"
            "--room-id 1364239598 か SUZULEAGUE_PROJECT_ID で本番のルームを指定してください"
        )
    pages = build_pages(room_id)
    print(f"ルームID: {room_id}")

    if args.check:
        problems = check_pages(pages, args.url)
        for p in problems:
            print(f"✗ {p}")
        if problems:
            print("→ 書き出して cloud-server にコミットし、デプロイし直してください")
            return 1
        print(f"✓ {args.url} の画面 {len(pages)} 件は、すべて手元から作ったものと一致しています")
        return 0

    changed = write_pages(pages, args.server_dir / "public")
    if not changed:
        print("変更なし（cloud-server の public/ はすでに最新です）")
        return 0
    for name in changed:
        print(f"更新: public/{name}")
    print(f"→ {args.server_dir} で差分を確認してコミットし、master に入れると Render がデプロイします")
    return 0


if __name__ == "__main__":
    sys.exit(main())

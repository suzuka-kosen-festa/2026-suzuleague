"""画面の書き出し・本番との照合のテスト（ネットワーク不要）。"""

from __future__ import annotations

import urllib.error

import pytest

from suzuleague.cloud import ENV_PROJECT_ID
from suzuleague.publish import build_pages, check_pages, main, write_pages


@pytest.fixture
def pages() -> dict[str, str]:
    return build_pages("1364239598")


class TestBuildPages:
    def test_contains_all_pages(self, pages):
        assert set(pages) == {"suzuleague.html", "host.html", "player.html"}

    def test_audience_page_uses_given_room(self, pages):
        assert '"1364239598"' in pages["suzuleague.html"]


class TestWritePages:
    def test_reports_only_changed_files(self, tmp_path, pages):
        assert sorted(write_pages(pages, tmp_path)) == ["host.html", "player.html", "suzuleague.html"]
        assert write_pages(pages, tmp_path) == []
        (tmp_path / "host.html").write_text("古い画面", encoding="utf-8")
        assert write_pages(pages, tmp_path) == ["host.html"]

    def test_missing_directory(self, tmp_path, pages):
        with pytest.raises(FileNotFoundError):
            write_pages(pages, tmp_path / "nowhere")


class TestCheckPages:
    def test_all_match(self, pages):
        live = {f"https://x/{k}": v for k, v in pages.items()}
        assert check_pages(pages, "https://x/", fetch=live.__getitem__) == []

    def test_detects_stale_page(self, pages):
        live = {f"https://x/{k}": v for k, v in pages.items()}
        live["https://x/suzuleague.html"] = "古い問題のページ"
        problems = check_pages(pages, "https://x", fetch=live.__getitem__)
        assert len(problems) == 1
        assert problems[0].startswith("suzuleague.html")

    def test_reports_unreachable_page(self, pages):
        def fetch(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        assert len(check_pages(pages, "https://x", fetch=fetch)) == 3


class TestMain:
    def test_refuses_dev_room(self, monkeypatch, tmp_path):
        """開発用ルームのまま本番に出すと、観客のスマホに何も表示されない。"""
        monkeypatch.delenv(ENV_PROJECT_ID, raising=False)
        with pytest.raises(SystemExit):
            main(["--server-dir", str(tmp_path)])

    def test_writes_to_public(self, tmp_path):
        (tmp_path / "public").mkdir()
        assert main(["--room-id", "1364239598", "--server-dir", str(tmp_path)]) == 0
        assert (tmp_path / "public" / "host.html").exists()
        assert '"1364239598"' in (tmp_path / "public" / "suzuleague.html").read_text(
            encoding="utf-8"
        )

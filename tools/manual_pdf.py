"""使い方マニュアル（docs/manual/*.html）を、撮った画面の写真を埋め込んだ PDF にする。

先に `tools/manual_shots.py` で画面を撮っておき、その書き出し先を渡す。
写真は PDF の中にだけ入れ、リポジトリには置かない（画面が変わったら撮り直して作り直す）。

    uv run --with "playwright==1.56.0" --with pillow --with qrcode python tools/manual_pdf.py <撮った画面のフォルダ>
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANUAL = REPO / "docs" / "manual"
BASE_URL = "https://suzuleague-cloud.onrender.com"

# 原稿 → 書き出す PDF の名前
DOCUMENTS = {
    "host.html": "スズリーグ_司会・進行マニュアル.pdf",
    "performer.html": "スズリーグ_出演者マニュアル.pdf",
}
# 原稿の data-qr="名前" に入れる QR コード
QR_URLS = {
    "host": f"{BASE_URL}/host.html",
    "audience": f"{BASE_URL}/suzuleague.html",
    "view": f"{BASE_URL}/stage/view/",
    "play": f"{BASE_URL}/stage/play/",
    "player": f"{BASE_URL}/player.html",
}

FOOTER = """<div style="width:100%;font-size:8px;color:#8a909c;padding:0 14mm;display:flex;justify-content:space-between;
font-family:'Hiragino Sans',sans-serif"><span>{title}</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>"""


def to_jpeg(shots: Path, out: Path, meta: dict) -> None:
    """PNG のままだと PDF が重くなるので、幅を抑えた JPEG にする。

    ステージ画面の問題文（本番の問題の絵）は、画像そのものを塗りつぶす。manual.js も上から隠すが、
    それだけだと PDF から画像を取り出せば読めてしまう。
    """
    from PIL import Image, ImageDraw

    for png in sorted(shots.glob("*.png")):
        with Image.open(png) as im:
            im = im.convert("RGB")
            draw = ImageDraw.Draw(im)
            for sprite in meta.get(png.stem, {}).get("sprites", []):
                if sprite["name"] == "問題":
                    x, y, w, h = sprite["box"]
                    draw.rectangle(
                        [x / 100 * im.width, y / 100 * im.height, (x + w) / 100 * im.width, (y + h) / 100 * im.height],
                        fill=(229, 231, 235),
                    )
            if im.width > 1600:
                im = im.resize((1600, round(im.height * 1600 / im.width)), Image.LANCZOS)
            im.save(out / f"{png.stem}.jpg", quality=84, optimize=True)


def qr_svgs() -> dict[str, str]:
    import qrcode
    import qrcode.image.svg

    svgs = {}
    for name, url in QR_URLS.items():
        buf = io.BytesIO()
        qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=2).save(buf)
        svg = buf.getvalue().decode()
        svgs[name] = svg[svg.index("<svg"):]
    return svgs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使い方マニュアルを PDF にする")
    parser.add_argument("shots", type=Path, help="tools/manual_shots.py で撮った画面のフォルダ")
    parser.add_argument("--out", type=Path, default=MANUAL, help="PDF の書き出し先")
    parser.add_argument("--only", choices=sorted(DOCUMENTS), help="この原稿だけ作る")
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    meta = json.loads((args.shots / "shots.json").read_text())
    qr = qr_svgs()
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        images = Path(tmp)
        to_jpeg(args.shots, images, meta)
        browser = pw.chromium.launch()
        for source, pdf_name in DOCUMENTS.items():
            if args.only and source != args.only:
                continue
            page = browser.new_page()
            page.add_init_script(
                f"window.SHOTS = {json.dumps(meta, ensure_ascii=False)};"
                f"window.SHOT_BASE = {json.dumps(images.as_uri() + '/')};"
                f"window.QR = {json.dumps(qr)};"
            )
            page.goto((MANUAL / source).as_uri())
            page.wait_for_function("() => window.MANUAL_READY === true", timeout=30000)
            lost = page.evaluate("() => [...document.querySelectorAll('.pin.lost, figure.missing')].map(e => e.closest('figure')?.dataset.shot + ' ' + (e.dataset.part || e.dataset.sprite || ''))")
            title = page.title()
            out = args.out / pdf_name
            page.pdf(
                path=str(out), prefer_css_page_size=True, print_background=True,
                display_header_footer=True, header_template="<span></span>", footer_template=FOOTER.format(title=title),
            )
            page.close()
            print(f"{out.relative_to(REPO) if out.is_relative_to(REPO) else out}（{out.stat().st_size / 1e6:.1f} MB）")
            for item in lost:
                print(f"  ✗ 位置がわからない注釈・画像: {item}")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

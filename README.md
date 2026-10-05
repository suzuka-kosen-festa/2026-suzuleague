# 2026-suzuleague

鈴鹿高専 高専祭2026 ステージイベント「スズリーグ」の進行システム。

テレビ番組「ネプリーグ」の「パーセントバルーン」をベースにしたクイズイベント。
**プロジェクターは使わない。** 司会と観客は自分のスマホ、出演者はステージに置いた端末の Scratch 画面で見る。
進行の中心は裏方PCで動く Python で、スマホの画面とは Render 上の cloud サーバを介してやり取りする。

```
司会のスマホ（司会者画面）   ─操作─┐
ステージの端末（Scratch画面）─回答─┼─▶ cloud サーバ（Render）◀─▶ 裏方PC（Python：進行・採点）
観客のスマホ（観客ページ）   ─成績─┘     │
                ◀──── 進行の配信（クラウド変数）──┘
```

| 画面 | URL | 使う人 |
|---|---|---|
| 観客ページ | <https://suzuleague-cloud.onrender.com/suzuleague.html>（QR: [docs/qr/](docs/qr/)） | 観客 |
| Scratch画面（出演者用） | <https://turbowarp.org/1364239598?cloud_host=wss://suzuleague-cloud.onrender.com> | ステージの出演者（数字キーで回答） |
| 出演者の回答画面（予備） | <https://suzuleague-cloud.onrender.com/player.html> | Scratch が使えないときの出演者（4桁の合言葉が要る） |
| 司会者画面 | <https://suzuleague-cloud.onrender.com/host.html> | 司会（合言葉が要る） |

## ドキュメント

初見の人は上から順に読むのがおすすめ。

| ドキュメント | 内容 | 対象読者 |
|---|---|---|
| [**しくみの図解**](https://htmlpreview.github.io/?https://github.com/suzuka-kosen-festa/2026-suzuleague/blob/main/docs/explainer.html) | システムの仕組みを図で説明（専門用語なし） | **全員（プログラム未経験でも読める）** |
| [docs/status.md](docs/status.md) | 進捗状況・残タスク・未解決の論点・リスク | 全員（現状把握） |
| [docs/users.md](docs/users.md) | 使う人（司会・出演者・観客など）と、それぞれが見る画面 | 全員（画面や機能を足す前に読む） |
| [docs/demo/README.md](docs/demo/README.md) | 10/7デモの段取り（端末・準備・本編・困ったとき） | デモをする人 |
| [docs/scratch/README.md](docs/scratch/README.md) | Scratch担当に渡すもの（問題文リストの取り込み手順） | Scratch担当 |
| [docs/architecture.md](docs/architecture.md) | 全体構成・設計判断の理由・レイヤー構造・データフロー | 全員（まず読む） |
| [docs/game-rules.md](docs/game-rules.md) | ゲームルール・進行ステートの遷移図・採点仕様 | 全員 |
| [docs/protocol.md](docs/protocol.md) | クラウド変数の通信仕様と、スマホ画面が使う HTTPS API | Scratch担当・開発者 |
| [docs/development.md](docs/development.md) | セットアップ・動作確認・既知の落とし穴・本番チェックリスト | 開発者 |
| [docs/イベント責任者作成の企画書.md](docs/イベント責任者作成の企画書.md) | イベントの企画書（ルール・タイムテーブル・台本） | 全員 |

「しくみの図解」の実体は [docs/explainer.html](docs/explainer.html)。
GitHub上でクリックするとHTMLのソースが表示されてしまうので、
表のリンク（htmlpreview経由）から開くか、クローン後に
`open docs/explainer.html`（Windowsは `start docs\explainer.html`）で開く。

## クイックスタート

[uv](https://docs.astral.sh/uv/) が入っていればすぐ動く。

```bash
uv sync
uv run pytest        # テスト実行

# ネットにつながずに進行だけ試す。ブラウザで http://localhost:8000/host を開くと司会者画面で操作できる
uv run suzuleague --offline --web
```

ダッシュボードで `n`（next）を打つか、司会者画面の「次へ」を押すとゲームが1段階ずつ進む。

本番の cloud サーバにつなぐときは、`launcher/スズリーグ.app` を開いて「デモ」か「本番」を選べば、
ターミナルを使わずに起動できる（操作はすべて司会者画面で行う）。
スマホから操作する手順（合言葉の設定など）は
[docs/development.md の「司会者画面」](docs/development.md#司会者画面スマホで進行を操作する)、
デモの段取りは [docs/demo/README.md](docs/demo/README.md) を参照。

## ソース構成

| ファイル | 役割 |
|---|---|
| `src/suzuleague/models.py` | ドメインモデル (Question/Team/RoundResult) |
| `src/suzuleague/questions.py` | 問題セット管理（コード内管理）・Scratch貼り付け用エクスポート |
| `src/suzuleague/engine.py` | ゲーム進行ステートマシン・採点（通信非依存の純ロジック） |
| `src/suzuleague/protocol.py` | クラウド変数プロトコルのエンコード/デコード |
| `src/suzuleague/cloud.py` | クラウド変数の接続ブリッジ（送信・受信・resync・ping応答） |
| `src/suzuleague/controller.py` | 進行操作の窓口（CLI・司会者画面・Scratch・出演者の回答を直列化） |
| `src/suzuleague/host.py` | 司会者画面・回答画面の中継（Render 経由）・出演者の合言葉・予備サーバ |
| `src/suzuleague/host.html` / `player.html` | 司会者画面・出演者の回答画面 |
| `src/suzuleague/publish.py` | 3画面の cloud-server への書き出しと本番との照合 |
| `src/suzuleague/labels.py` | ステートの表示名（CLIと画面で共通） |
| `src/suzuleague/dashboard.py` | 起動とCLIダッシュボード（中継・予備サーバも立ち上げる） |
| `src/suzuleague/sim_scratch.py` | Scratch側シミュレータ（開発用） |
| `src/suzuleague/teams.py` | チーム構成の読み込み（名前・メンバー・登場順） |
| `src/suzuleague/audience.py` | 観客ページの生成（問題文を埋め込む） |
| `src/suzuleague/audience_template.html` | 観客ページ（自己採点・観客ランキング・全体結果） |
| `src/suzuleague/loadtest.py` | 同時接続数・観客ランキングの一斉送信の負荷テスト（開発用） |
| `tests/` | ユニットテスト（ネットワーク不要。119件） |
| `launcher/` | ダッシュボードの起動アプリ（`スズリーグ.app`）とターミナル版（`*.command`） |
| `docs/scratch/` | Scratch担当に渡す問題文リストと取り込み手順 |
| `docs/qr/` | QRコードと印刷用ページ |

## 開発体制

- このリポジトリ: Python / scratchattach / uv と、スマホの画面（HTML）
- cloud サーバ: [inouekoshi/cloud-server](https://github.com/inouekoshi/cloud-server)（TurboWarp の cloud-server のフォーク。Render で稼働。
  デモ後にこのリポジトリへまとめる予定 → [#45](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/45)）
- Scratch側: 別担当者が開発（本番プロジェクト [`1364239598`](https://scratch.mit.edu/projects/1364239598/)。出演者の手元の端末で使う → [#41](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/41)）
- 作業は main に直接コミットして push する（cloud-server の master への push は即本番デプロイ）
- 連絡・相談はDiscordで随時

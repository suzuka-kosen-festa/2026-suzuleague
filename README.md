# 2026-suzuleague

鈴鹿高専 高専祭2026 ステージイベント「スズリーグ」の進行システム（バックエンド）。

テレビ番組「ネプリーグ」の「パーセントバルーン」をベースにしたクイズイベントで、
Scratch（ステージ表示・回答UI）とPython（進行管理・採点）が
TurboWarpのクラウド変数を介して連携する。

```
Scratch(TurboWarp) <-> TurboWarp cloud <-> Python(scratchattach) [<-> Firestore(将来)]
```

## ドキュメント

初見の人は上から順に読むのがおすすめ。

| ドキュメント | 内容 | 対象読者 |
|---|---|---|
| [**しくみの図解**](https://htmlpreview.github.io/?https://github.com/suzuka-kosen-festa/2026-suzuleague/blob/main/docs/explainer.html) | システムの仕組みを図で説明（専門用語なし） | **全員（プログラム未経験でも読める）** |
| [docs/status.md](docs/status.md) | 進捗状況・残タスク・未解決の論点・リスク | 全員（現状把握） |
| [docs/users.md](docs/users.md) | 使う人（司会・出演者・観客など）と、それぞれが見る画面 | 全員（画面や機能を足す前に読む） |
| [docs/architecture.md](docs/architecture.md) | 全体構成・設計判断の理由・レイヤー構造・データフロー | 全員（まず読む） |
| [docs/game-rules.md](docs/game-rules.md) | ゲームルール・進行ステートの遷移図・採点仕様 | 全員 |
| [docs/protocol.md](docs/protocol.md) | Scratch⇔Python間のクラウド変数の通信仕様 | Scratch担当・バックエンド担当 |
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

# ターミナル1: 司会進行ダッシュボード
uv run suzuleague

# ターミナル2: Scratch側シミュレータ（Scratch実装なしで動作確認できる）
uv run python -m suzuleague.sim_scratch --auto
```

ダッシュボードで `n`（next）を打つとゲームが1段階ずつ進行し、
シミュレータ側に「ステージ画面」の模擬表示が流れる。
その他のコマンド・オプションは [docs/development.md](docs/development.md) 参照。

## ソース構成

| ファイル | 役割 |
|---|---|
| `src/suzuleague/models.py` | ドメインモデル (Question/Team/RoundResult) |
| `src/suzuleague/questions.py` | 問題セット管理（コード内管理）・Scratch貼り付け用エクスポート |
| `src/suzuleague/engine.py` | ゲーム進行ステートマシン・採点（通信非依存の純ロジック） |
| `src/suzuleague/protocol.py` | クラウド変数プロトコルのエンコード/デコード |
| `src/suzuleague/cloud.py` | TurboWarp cloud接続ブリッジ（送信・受信・resync） |
| `src/suzuleague/dashboard.py` | 司会進行用CLIダッシュボード |
| `src/suzuleague/sim_scratch.py` | Scratch側シミュレータ（開発用） |
| `src/suzuleague/teams.py` | チーム構成の読み込み（名前・メンバー・登場順） |
| `src/suzuleague/audience.py` | 観客用ページ（スマホ参加画面）の生成 |
| `src/suzuleague/audience_template.html` | 観客用ページのテンプレート |
| `src/suzuleague/loadtest.py` | cloudサーバの同時接続数の負荷テスト（開発用） |
| `tests/` | ユニットテスト（エンジン・プロトコル） |

## 開発体制

- バックエンド（このリポジトリ）: Python / scratchattach / uv
- Scratch側: 別担当者が[既存プロジェクト](https://scratch.mit.edu/projects/493900220/)をリミックスして開発
- 連絡・相談はDiscordで随時

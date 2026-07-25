# 開発ガイド

セットアップから動作確認・トラブルシューティングまで、開発に必要な情報をまとめる。

## セットアップ

必要なのは [uv](https://docs.astral.sh/uv/) のみ（Python本体もuvが自動で用意する）。

```bash
git clone https://github.com/inouekoshi/snctfes2026-suzuleague.git
cd snctfes2026-suzuleague
uv sync
uv run pytest   # 動作確認（全テストが通ればOK）
```

主な依存パッケージ:

| パッケージ | 用途 |
|---|---|
| [scratchattach](https://github.com/TimMcCool/scratchattach) | TurboWarp cloudへの接続（Scratch APIラッパー） |
| [websocket-client](https://github.com/websocket-client/websocket-client) | 生WebSocket接続（`fetch_all_vars` と `loadtest`） |
| [rich](https://github.com/Textualize/rich) | CLIダッシュボードの表示 |
| pytest (dev) | テスト |

### 依存は本番まで更新しない

`pyproject.toml` のバージョンに**上限を切ってある**。本番（2026/10/31〜11/1）まで
挙動を変えないためで、意図的な措置。

とくに **scratchattach は更新しないこと**。下記「既知の落とし穴」に書いた罠は
すべて 2.2.1 での実測で、更新すると前提が崩れる可能性がある。
`uv.lock` もコミットしてあるので、`uv sync` すれば全員が同じバージョンになる。

セキュリティ上の理由などで更新が必要になった場合は、
**更新後に必ずスモークテストとE2Eを通す**こと（ユニットテストは通信層を見ていない）。

自動更新ツール（Renovate）は入れていない。理由は
[#8](https://github.com/suzuka-kosen-festa/snctfes2026-suzuleague/issues/8) を参照。

## 動作確認の方法（3段階）

### 1. オフライン: ロジックだけ確認

ネットワーク不要。ゲーム進行と採点の挙動を手元で試す。

```bash
uv run suzuleague --offline
```

### 2. スモークテスト: cloud疎通確認

TurboWarp cloudへの接続・書き込み・読み戻し・受信待機を一通り実行する。

```bash
uv run python -m suzuleague.cloud
```

### 3. E2E: ダッシュボード × シミュレータ

ターミナルを2枚開き、実際のTurboWarp cloud経由で両側を動かす。
**Scratch側の実装がなくても**本番同等の通信経路を検証できる。

```bash
# ターミナル1: 司会ダッシュボード
uv run suzuleague

# ターミナル2: Scratch側シミュレータ
uv run python -m suzuleague.sim_scratch          # 手動回答（数値を入力して送信）
uv run python -m suzuleague.sim_scratch --auto   # 自動回答（乱数）
uv run python -m suzuleague.sim_scratch --auto --fixed 40  # 自動回答（固定値）
```

ダッシュボードで `n` を押して進行させると、シミュレータ側に
「ステージ画面」の模擬表示が流れる。回答受付ステートで
シミュレータから回答を送るとダッシュボードに届く。

## ダッシュボードのコマンド

| コマンド | 動作 |
|---|---|
| `next` / `n` | 進行を1段階進める（[遷移図](./game-rules.md#遷移図)参照） |
| `answer <0-100>` / `a` | 回答を入力（Scratchからの回答の代行。上書き可） |
| `status` / `s` | 現在の状況を表示 |
| `teams` / `t` | 全チームのスコア一覧・優勝表示 |
| `resync` | クラウド変数の全状態を再送（Scratch側リロード後に使う） |
| `help` / `h` | ヘルプ |
| `quit` / `exit` | 終了 |

## 設定

| 設定 | 方法 | デフォルト |
|---|---|---|
| 接続先ルームID | `--project-id` または環境変数 `SUZULEAGUE_PROJECT_ID` | `suzuleague-dev`（開発用）。**本番は `1364239598`** |
| 接続先cloudサーバ | `--cloud-host` または環境変数 `SUZULEAGUE_CLOUD_HOST` | `wss://clouddata.turbowarp.org`（公開サーバ） |
| チーム構成 | `--teams teams.json` または環境変数 `SUZULEAGUE_TEAMS` | 「チーム1」〜「チーム4」 |
| ぴったり賞 | `uv run suzuleague --perfect-bonus 10` | 無効（0）。**本番は不採用なので付けない** |
| オフライン起動 | `uv run suzuleague --offline` | オンライン |

`--cloud-host` / `SUZULEAGUE_CLOUD_HOST` は `suzuleague` / `sim_scratch` /
`suzuleague.cloud`（スモークテスト）/ `loadtest` のすべてで共通に効く。

ルームIDについて: TurboWarp cloudは任意の文字列IDで「部屋」を作れる。
開発中は `suzuleague-dev` を使い、**本番はScratch担当が用意した
プロジェクトのID `1364239598`**に切り替える（Scratch側は本物のプロジェクトIDでしか繋げないため）。
本番プロジェクト: <https://scratch.mit.edu/projects/1364239598/>（TurboWarpで開くときは
末尾に `?cloud_host=wss://suzuleague-cloud.onrender.com` を必ず付ける）。

### チーム名・メンバーを設定する

本番のチーム名とメンバーは企画側から受け取る。**メンバーの氏名は個人情報**なので
リポジトリには含めず、JSONファイルを外から渡す（`teams.json` は .gitignore 済み）。

```bash
cp teams.example.json teams.json   # 中身を本番の値に書き換える
uv run suzuleague --teams teams.json
```

書式:

```json
[
  {"number": 1, "name": "チーム名", "members": ["回答順に氏名を並べる", "2人目"]}
]
```

- **ファイルに書いた順が登場順**になる
- `number` は表示とプロトコル（`P2S_TEAM`）で使うチーム番号で、登場順とは独立に指定できる
  （企画側が「3組を1番目に登場」と指定してきても対応できる）
- `members` は任意。省略しても動く

### 接続先cloudサーバの切り替え

本番は公開サーバの128接続上限を避けるためセルフホストのサーバを使う
（[architecture.md](./architecture.md#ホスト先-render-の無料枠2026-07-23決定)）。
接続先はコード変更なしで差し替えられる。

```bash
# 本番（セルフホスト。この形で司会PCの環境変数に入れておく）
export SUZULEAGUE_CLOUD_HOST=wss://suzuleague-cloud.onrender.com
uv run suzuleague

# 単発で指定する場合
uv run suzuleague --cloud-host wss://suzuleague-cloud.onrender.com
```

サーバの管理は Render CLI から行う（`brew install render` → `render login`）。

```bash
render services                                    # 一覧
render logs --resources srv-d9go9isvikkc739qi500   # ログ
render deploys create srv-d9go9isvikkc739qi500     # 再デプロイ
```

URLの扱いで事故りやすい点を先回りして処理してある。

- **`https://` を貼っても動く**。ホスティングの管理画面は `https://` 形式のURLを
  表示するので、`wss://` に自動で読み替える（`http://` は `ws://`）
- **外部ホストへの `ws://`（TLSなし）は起動時にエラーで止まる**。TurboWarpのページは
  HTTPS配信のため、平文wsはブラウザのmixed contentブロックでScratch側から繋がらない。
  当日まで気付けない類の事故なので、Python側で先に弾いている
- `ws://localhost:9080` など**ローカルホストへの平文wsは許可**（下記の手元検証用）

**Scratch側**は URL に `?cloud_host=wss://...` を付けて開く。Python側と同じ値を使うこと。
片方だけ切り替えると、エラーは出ないまま互いの変数が見えない状態になる。

### 手元でcloud-serverを動かして試す

セルフホスト先と同じサーバをローカルに立てられる。接続数の検証もこれで行う。

本番で使うのは本家ではなく
[inouekoshi/cloud-server](https://github.com/inouekoshi/cloud-server)（fork）。
人数上限を環境変数で変えられるようにしてある。

```bash
git clone https://github.com/inouekoshi/cloud-server && cd cloud-server
npm install
MAX_CLIENTS=300 npm start       # ws://localhost:9080 で起動

# 別ターミナルから
uv run python -m suzuleague.cloud --cloud-host ws://localhost:9080   # 疎通確認
uv run python -m suzuleague.loadtest --cloud-host ws://localhost:9080 --ramp 10,50,128,140
```

`MAX_CLIENTS` を指定しないと本家と同じ128人が上限になる。
ポートは `PORT` で変えられる（`PORT=9081 npm start`）。

### 観客用ページを更新する

観客がスマホから開く画面は cloud サーバの `public/` から配信している。
**問題文がページに埋め込まれているので、問題を差し替えたら必ず再生成する。**

```bash
# fork した cloud-server が隣にある前提
uv run python -m suzuleague.audience -o ../cloud-server/public/suzuleague.html

cd ../cloud-server
git add public/suzuleague.html && git commit -m "観客ページを更新" && git push
#   → Render が自動でデプロイする（数分）
```

生成物には**正解値を含めない**（先に見えてしまうため）。
これは `tests/test_audience.py` で自動確認している。

接続先: <https://suzuleague-cloud.onrender.com/suzuleague.html>

### 本番サーバが落ちたときの代替手段

Render のサーバが当日不調だった場合、司会PC上でサーバを動かして
[Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
で外部公開する。アカウント登録不要・無料・WebSocketは既定で通る。

**この手順は2026-07-23に実地で確認済み**（下記の実測を参照）。

```bash
# 事前に入れておく（当日その場では入れられない）
brew install cloudflared

# 1. 司会PCで cloud-server を起動（fork のディレクトリにて）
MAX_CLIENTS=300 npm start

# 2. 別ターミナルでトンネルを張る
cloudflared tunnel --url http://localhost:9080
#   → https://xxxx-xxxx.trycloudflare.com が発行される（数秒）
```

発行されたURLを**Python側とScratch側の両方**に設定する。
`https://` のまま渡してよい（`wss://` へ自動で読み替える）。

```bash
uv run suzuleague --cloud-host https://xxxx-xxxx.trycloudflare.com
```

観客ページも同じトンネルから配信される
（`https://xxxx-xxxx.trycloudflare.com/suzuleague.html`）。

実測（2026-07-23）:

| 確認項目 | 結果 |
|---|---|
| 観客ページの配信 | HTTP 200 / 1.08秒 |
| cloud変数の書き込み・読み戻し | 一致 |
| 20接続での配信到達 | 20/20接続・19/19到達・遅延58.4ms |

制約と注意:

- **同時200リクエストの上限**があり、観客参加には余裕がない。最終手段と考える
- **URLは起動のたびに変わる**。Scratch側に渡し直す必要があるので、
  切り替えるならステージ画面のリロードもセットになる
- 司会PCのネットワークが落ちると全滅する（Renderなら司会PCと独立している）
- `cloudflared` は**事前にインストールしておくこと**。
  当日ネットワークが不調な状況で `brew install` はできない

観客参加を諦めて司会PCとステージ画面だけ繋ぐなら、
同一LAN内の `ws://192.168.x.x:9080` でも動く（TurboWarpをローカル配信する場合に限る）。

## テスト

```bash
uv run pytest        # 全部（ネットワーク不要・1秒未満）
uv run pytest -k exhibition   # 絞り込み例
```

| ファイル | 対象 |
|---|---|
| `tests/test_engine.py` | 状態遷移・採点境界値（ぴったり/0到達）・エキシビション移行・優勝判定・入力検証 |
| `tests/test_protocol.py` | Snapshot→クラウド変数のエンコード、受信値のパース（"45.0"等の揺れ・範囲外） |

cloud通信層（`cloud.py`）は実サーバ依存のためユニットテスト対象外。
変更したら上記スモークテスト＋E2Eで確認すること。

## 既知の落とし穴（scratchattach × TurboWarp）

実測で確認済みの罠。**どれもエラーを出さずに静かに失敗する**ので必読。

### 1. `set_vars()`（一括送信）は使用禁止

scratchattachの `set_vars()` は複数のJSONを改行連結して**1つのWebSocket
フレーム**で送るが、TurboWarpサーバはこれを不正フレームとして無視し、
**以降その接続からの送信をすべて破棄する**（接続が汚染される）。
本リポジトリでは必ず `set_var()` で1変数ずつ送る実装にしている
（`cloud.py` の `CloudBridge.push()` のコメント参照）。

### 2. `get_var()` / `get_all_vars()` でTurboWarpの既存値は読めない

TurboWarpサーバは新規接続に現在値の初期ダンプを送るが、scratchattachの
イベント処理は**接続後500ms以内のメッセージを捨てる**ため、初期ダンプが
届かない。既存値が必要な場合は自前の `cloud.fetch_all_vars()`
（生WebSocketで初期ダンプを読む）を使うこと。

### 3. 同一IPからの接続レート制限（公開サーバのみ）

短時間に接続を繰り返すと、ハンドシェイクがタイムアウトするようになる
（1〜2分のクールダウンで回復）。テストスクリプトの連続実行や
TurboWarp画面の連続リロードで発生しやすい。本番前のリハーサルでは
むやみに再起動しないこと。

なお **cloud-server のソースにはIP単位の制限が一切実装されていない**
（`ConnectionManager.js` にあるのは30秒間隔のping/pongによる死活監視だけ）。
この制限は公開サーバの前段のインフラによるものなので、
**セルフホストに切り替えれば発生しない**。会場Wi-Fiで観客が同一の
グローバルIPに集約されても問題にならない。

### 4. イベントハンドラの停止漏れでプロセスが終わらない

`cloud.events()` のスレッドは非デーモンなので、終了時に `events.stop()` を
呼ばないとプロセスが残る。`CloudBridge.disconnect()` /
`ScratchSimulator.disconnect()` が対応済み。新しくイベントを使うコードを
書くときは注意。

### 5. TurboWarp cloudの値は全員切断で消える

サーバはインメモリ。全クライアントが切断されると変数は消える。
Python側が常に正の状態を持ち、`resync` で再送できる設計を崩さないこと。

## 通信仕様を変更するとき

1. `src/suzuleague/protocol.py` の変数定義・エンコードを変更
2. `docs/protocol.md`（Scratch担当との共有仕様書）を同時に更新
3. `tests/test_protocol.py` を更新
4. **Scratch担当に変更をDiscordで連絡**（Scratch側の改修が必要なため）

## リリース（本番投入）チェックリスト

### 事前準備

- [x] ~~本番問題をアンケート集計スプレッドシートから `questions.py` に投入~~ → **完了**（20問＋予備8問）
- [x] ~~ぴったり賞の採否をイベント責任者に確認~~ → **不採用**（2026-07-23）。`--perfect-bonus` は付けない
- [x] ~~チームの人数と問題数の対応を確認~~ → **5問固定・司会が回答者を指名**（2026-07-24）。実装変更なし
- [ ] `uv run python -m suzuleague.questions` の出力をScratch側リストに再取り込み
- [ ] `SUZULEAGUE_PROJECT_ID` を本番プロジェクトIDに設定
- [ ] `SUZULEAGUE_CLOUD_HOST=wss://suzuleague-cloud.onrender.com` を司会PCに設定し、
      **Scratch側の `?cloud_host=` と一致していることを確認**（片方だけだと無言で繋がらない）
- [ ] **Scratch側との結合テスト**（シミュレータではなく実物と繋ぐ。ここが最大の未知数）
- [ ] チーム名が確定したら `teams.json` を作成（`--teams teams.json`）
- [ ] 問題を差し替えた場合は観客ページを再生成してデプロイ
      （問題文がページに埋め込まれているため）
- [ ] 観客ページを**実機のスマホ**で開いて表示を確認し、QRコードを発行・印刷
- [ ] Render 無料枠の残インスタンス時間を確認（アカウント全体で月750時間の共有）
- [ ] 会場ネットワークでE2Eリハーサル（企画書のリハーサル項目参照）

### 当日（開演前）

- [ ] **開演30分前** に <https://suzuleague-cloud.onrender.com> をブラウザで開く
      （15分無通信でスピンダウンする。実測での復帰は22.8秒、公称は約1分）
- [ ] ダッシュボードを起動して接続を確立する（以降 `HEARTBEAT` が15秒毎に流れるので眠らない）
- [ ] ステージ画面を `?cloud_host=` 付きURLで開き、`resync` で表示が復帰することを確認
- [ ] 進行不能時の代替手段を確認（`answer` コマンドでの代行入力、`resync`、
      [バックアップ手順](#本番サーバが落ちたときの代替手段)）

## プロジェクトの経緯・意思決定の記録

- 設計判断の理由: [architecture.md](./architecture.md#主要な設計判断とその理由)
- 通信は cloud 変数、本番は**セルフホストのサーバ**（2026-07-23 デプロイ済み）
- UIは**CLIのまま本番へ**。Web UIは作らない（[理由](./architecture.md#4-ダッシュボードはまずcli)）
- **観客スマホ参加は本番スコープに含む**（企画書・司会台本に組み込み済みのため。
  当初は次フェーズ送りとしていたが2026-07-22に格上げ）。回答は端末内で自己採点し
  サーバへ送らない方式（2026-07-23決定）
- **費用は0円**。予算枠がないため経費申請もしない（2026-07-23決定）
- **本番まで依存を更新しない**（2026-07-23決定）。Renovateも導入しない
- 期限: 本番は2026/10/31〜11/1だが、8月中の完成を目指す
- 技術的な詰まり・仕様変更はDiscordで即時相談する運用（上司指示）

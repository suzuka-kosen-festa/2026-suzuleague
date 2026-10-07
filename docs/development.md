# 開発ガイド

セットアップから動作確認・トラブルシューティングまで、開発に必要な情報をまとめる。

## セットアップ

必要なのは [uv](https://docs.astral.sh/uv/) のみ（Python本体もuvが自動で用意する）。

```bash
git clone https://github.com/suzuka-kosen-festa/2026-suzuleague.git
cd 2026-suzuleague
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
[#8](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/8) を参照。

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
| `resync` | クラウド変数の全状態を再送（Scratch側リロード後に使う。司会者画面の「困ったとき」にも同じボタンがある） |
| `help` / `h` | ヘルプ |
| `quit` / `exit` | 終了 |

## 司会者画面（スマホで進行を操作する）

司会は手元のスマホで進行を操作する（[users.md](./users.md)）。スマホから裏方PCへは直接つながず、
**Render の cloud-server を郵便受けにして中継**する。

```
司会のスマホ ──操作を預ける──▶ Render（/api/host/*） ◀──0.4秒ごとに取りに行く── 裏方PC（Python）
            ◀──状態を見る────                     ◀──状態を預ける──────────
```

### 使い方

1. Render のサービスに環境変数 **`HOST_TOKEN`**（合言葉）を設定する。未設定だとAPIは無効（503）
2. 裏方PCで同じ合言葉を **`SUZULEAGUE_HOST_TOKEN`** に入れてダッシュボードを起動する。
   ふだんは `launcher/スズリーグ.app` を開くだけでよい。合言葉の入力（初回だけ）・Render の起動・
   `publish --check`・ブラウザで予備の司会者画面を開くところまで自動で行う（下の「アプリで起動する」）

   ```bash
   export SUZULEAGUE_HOST_TOKEN='（合言葉）'
   uv run suzuleague --web
   #   司会者画面（スマホ）: https://suzuleague-cloud.onrender.com/host.html
   #   司会者画面（予備）:   http://localhost:8000/host
   ```

3. 司会のスマホで `https://suzuleague-cloud.onrender.com/host.html` を開き、合言葉を入力する
   （スマホに保存されるので次からは聞かれない）

### アプリで起動する

`launcher/スズリーグ.app` を開き、「デモ（1チーム5問）」か「本番」を選ぶ。**ターミナルは開かない**。
ダッシュボードは裏で動き、操作はすべて司会者画面で行う（Scratch への送り直しも「困ったとき」のボタンでできる）。
PCのブラウザに開く司会者画面（`localhost`）は予備で、観客ランキングの操作はできない（スマホの司会者画面で行う）。

| 選ぶもの | チーム構成 |
|---|---|
| デモ（1チーム5問） | `docs/demo/teams-demo.json` |
| 本番 | リポジトリ直下の `teams.json`（なければ止まる） |

どちらも本番の cloud サーバ・ルーム `1364239598` につなぐ。

- 合言葉は初回に聞かれ、`launcher/settings.env`（Git には入らない）に保存される。起動のたびに Render の `HOST_TOKEN` と一致するか確かめ、違えば入れ直しを求める（Render 側で合言葉を変えたときも、次の起動で聞かれる）
- 起動中にもう一度開くと「司会者画面を開く」「終了する」を選べる。司会者画面には終了ボタンを置いていない（本番中にスマホで誤って押さないため）
- ダッシュボードのログは `launcher/run/dashboard.log`（前回分は `dashboard.prev.log`）
- 前のダッシュボードが残っていてポート8000が使われていると、起動前に止まって知らせる
- **進行は操作のたびに `launcher/run/game-demo.json`（本番は `game-production.json`）に保存する。** 裏方PCが落ちたり「終了する」を押したりしても、
  次にアプリを開くと「前回の続きがあります」と出て、**「続きから」で落ちる前の状態**（何問目か・バルーン・届いていた回答・出演者の合言葉）から再開できる（[#51](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/51)）。
  「最初から」を選ぶと、前回の分は `game-*.prev.json` に1つだけ残る。**本番の開演前（リハーサルの後）は「最初から」を選ぶ**

| ファイル | 役割 |
|---|---|
| `launcher/app.applescript` | アプリの画面（ダイアログ）。書き換えたら `launcher/build-app.sh` で `.app` を作り直す |
| `launcher/app.sh` | アプリの裏側（合言葉の確認・起動・終了） |
| `launcher/デモを起動.command` / `本番を起動.command` | **ターミナル版（予備）**。コマンドも打てる。終えるときは窓で `quit` |
| `launcher/launch.sh` | ターミナル版の中身 |
| `launcher/common.sh` | 両方で共通の設定（接続先・ルームID・合言葉の保存） |

CLIのコマンドもそのまま使える。CLI・司会者画面・Scratchのどこから操作しても、
同じ `GameController`（`controller.py`）を通るので食い違わない。

### 安全のための仕組み

| 仕組み | 防ぐもの |
|---|---|
| 合言葉（`X-Host-Token` ヘッダ。HTTPSで送る） | URLが漏れて進行を乗っ取られる。クラウド変数と違い、他の端末には流れない |
| 「次へ」に表示中のステートを添える（`expect_state`） | 通信の遅れで「次へ」が二重に届き、2段階進んでしまう |
| 裏方PCは起動前に溜まっていた操作を捨てる | 再起動直後に古い「次へ」が一気に実行される |
| 正解は押している間だけ表示 | 司会のスマホを周りに覗かれる |

### 出演者の回答画面（予備）

本番の出演者は、ステージに置いた端末の **Scratch 画面の数字キー**で回答する（`☁ S2P_ANSWER`。#41）。
スマホの回答画面（`https://suzuleague-cloud.onrender.com/player.html`）は、Scratch の端末が使えないときの予備。
回答は司会の操作と同じ郵便受けに預けられ、裏方PCが取り出して採点に使う。

- **観客のなりすまし対策として、チームごとの4桁の合言葉が要る。** 裏方PCの起動時に作られ、
  司会者画面とCLIの状態表示に出る。司会がチームの登場時に出演者へ伝える
- 合言葉の間違いが1分に30回続くと、30秒間すべての回答を止める（総当たり対策）
- 1つ前の問題への回答が遅れて届いた場合や、回答受付中でない場合は受け付けない
- 回答画面は誰でも開けるので、**正解は発表後にしか送らない**（`build_player_state`。`tests/test_host.py` で確認）
- 出演者から届かないときは、司会者画面の「代理入力」で入れる

### 観客ランキング

観客ページは、正解発表のたびに成績（20問の誤差の合計）を cloud-server に送る（`/api/score`）。
**未回答の問題は1問50として数える**。発表済みの問題数は裏方PCが送る状態（`player.revealed`）から
サーバが数えるので、途中から参加した人や答えるのをやめた人が上位に残ることはない。

- 順位は全体結果で全員のスマホに出る（チームの順位・優勝・景品の案内と一緒に）。チーム結果の画面では自分の順位だけを出す
- ニックネームは10文字まで。司会者画面の「観客ランキング」から、不適切な名前を上位表示から隠せる
- **リハーサルの後は、司会者画面の「ランキングをリセット」で消してから本番に臨む**
- 成績は端末の自己申告なので改ざんは防げない（文化祭のおまけ要素と割り切っている）

### QRコード

`docs/qr/` に置いてある。印刷するときは `docs/qr/print.html` をブラウザで開いて印刷する（A4縦・2枚）。

| ファイル | 読み取ると開く画面 | 使い方 |
|---|---|---|
| `audience.png` / `.svg` | 観客ページ | クラス委員が掲げる・各室長に送る |
| `player.png` / `.svg` | 出演者の回答画面（予備） | Scratch の端末が使えないときに控室・ステージ袖で使う（観客には見せない） |

URLを変えたら作り直す（依存に足さず、その場でだけ `qrcode` を使う）。

```bash
uv run --no-project --with "qrcode[pil]" python -c "
import qrcode, qrcode.image.svg as s
for n, u in {'audience': 'https://suzuleague-cloud.onrender.com/suzuleague.html',
             'player': 'https://suzuleague-cloud.onrender.com/player.html'}.items():
    qrcode.make(u, box_size=20, border=4).save(f'docs/qr/{n}.png')
    qrcode.make(u, image_factory=s.SvgPathImage).save(f'docs/qr/{n}.svg')"
```

### Render に届かないとき

`--web` を付けて起動しておけば、裏方PCのブラウザで `http://localhost:8000/host` を開いて
同じ画面で操作できる（合言葉なし・その場で実行）。CLIでの操作も常に使える。

### 画面を更新する

画面の元ファイルは `src/suzuleague/host.html`。Render へは cloud-server の `public/host.html` として
配信している。書き出しは観客ページと一緒に `publish` コマンドで行う（[観客用ページを更新する](#観客用ページを更新する)）。

## 設定

| 設定 | 方法 | デフォルト |
|---|---|---|
| 接続先ルームID | `--project-id` または環境変数 `SUZULEAGUE_PROJECT_ID` | `suzuleague-dev`（開発用）。**本番は `1364239598`** |
| 接続先cloudサーバ | `--cloud-host` または環境変数 `SUZULEAGUE_CLOUD_HOST` | `wss://clouddata.turbowarp.org`（公開サーバ） |
| チーム構成 | `--teams teams.json` または環境変数 `SUZULEAGUE_TEAMS` | 「チーム1」〜「チーム4」 |
| ぴったり賞 | `uv run suzuleague --perfect-bonus 10` | 無効（0）。**本番は不採用なので付けない** |
| オフライン起動 | `uv run suzuleague --offline` | オンライン |
| 進行の保存 | `--save-file launcher/run/game-demo.json`（操作のたびに保存） | 保存しない（`スズリーグ.app` は必ず付ける） |
| 続きから始める | `--resume`（`--save-file` と一緒に使う。付けなければ最初からで、前回分は `.prev` に残る） | 最初から |
| CLIなしで動かす | `--headless`（SIGTERM で終了。`スズリーグ.app` が使う） | CLIで入力を待つ |

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
cp teams.example.json teams.json   # リポジトリ直下に置き、中身を本番の値に書き換える
```

`launcher/スズリーグ.app` で「本番」を選ぶと、この `teams.json` を読んで起動する（CLI なら `uv run suzuleague --teams teams.json`）。
保存した進行はチーム構成と照合するので、`teams.json` を書き換えた後は前回の続きが「読めませんでした」になり、最初から始まる。

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
# 本番（セルフホスト。この形で裏方PCの環境変数に入れておく）
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

観客ページと司会者画面は cloud サーバ（[inouekoshi/cloud-server](https://github.com/inouekoshi/cloud-server)）の
`public/` から配信している。**観客ページには問題文が埋め込まれているので、問題を差し替えたら必ず書き出し直す。**

書き出しと本番との照合は `publish` コマンドにまとめてある。手でコピーしない。

```bash
# 1. 書き出す（隣に cloud-server がクローンしてある前提。場所が違えば --server-dir）
uv run python -m suzuleague.publish --room-id 1364239598

# 2. cloud-server で差分を確認してブランチを切り、PRを作る
cd ../cloud-server && git switch -c update-pages && git add public && git commit -m "画面を更新" && git push -u origin update-pages
#   → master にマージすると Render が自動でデプロイする（約90秒）

# 3. 本番の画面が手元から作ったものと一致するか確かめる
uv run python -m suzuleague.publish --room-id 1364239598 --check
```

- 接続先が開発用ルーム（`suzuleague-dev`）のままだと書き出しを拒否する（観客のスマホに何も映らなくなるため）
- 生成物には**正解値を含めない**（先に見えてしまうため）。`tests/test_audience.py` で自動確認している
- 2つのリポジトリに分かれているのは経緯によるもので、デモ後に1つにまとめる（[#45](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/45)）

接続先: <https://suzuleague-cloud.onrender.com/suzuleague.html>・<https://suzuleague-cloud.onrender.com/host.html>

### 本番サーバが落ちたときの代替手段

Render のサーバが当日不調だった場合、裏方PC上でサーバを動かして
[Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
で外部公開する。アカウント登録不要・無料・WebSocketは既定で通る。

**この手順は2026-07-23に実地で確認済み**（下記の実測を参照）。

```bash
# 事前に入れておく（当日その場では入れられない）
brew install cloudflared

# 1. 裏方PCで cloud-server を起動（fork のディレクトリにて。合言葉も Render と同じものを入れる）
MAX_CLIENTS=300 HOST_TOKEN='（合言葉）' npm start

# 2. 別ターミナルでトンネルを張る
cloudflared tunnel --url http://localhost:9080
#   → https://xxxx-xxxx.trycloudflare.com が発行される（数秒）
```

発行されたURLを**Python側とScratch側の両方**に設定する。
`https://` のまま渡してよい（`wss://` へ自動で読み替える）。

```bash
uv run suzuleague --cloud-host https://xxxx-xxxx.trycloudflare.com
```

**3つの画面とAPIも同じトンネルから配信される**（`/suzuleague.html`・`/host.html`・`/player.html`）。
ただし**URLが変わるので、印刷したQRは使えない**。新しいURLを司会・出演者に伝え、
観客にはその場でQRを作って見せる（`docs/development.md` の「QRコード」の手順でURLを差し替える）。
観客ランキングはサーバごとに別なので、切り替えた時点からの集計になる。

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
- 裏方PCのネットワークが落ちると全滅する（Renderなら裏方PCと独立している）
- `cloudflared` は**事前にインストールしておくこと**。
  当日ネットワークが不調な状況で `brew install` はできない

観客参加を諦めて裏方PCとステージ画面だけ繋ぐなら、
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
| `tests/test_publish.py` | 画面の書き出し（変わったファイルだけ報告）・本番との照合・開発用ルームでの書き出し拒否 |
| `tests/test_host.py` | 司会者画面・出演者の回答画面：表示用の状態（発表前に正解を含まない）、操作の実行（二重押し・不正値・他チームの合言葉・遅れて届いた回答）、Render中継、予備サーバの往復 |
| `tests/test_audience.py` | 観客ページの生成（正解を埋め込まない・ルームIDと問題文の埋め込み） |
| `tests/test_teams.py` | チーム構成JSONの読み込みと検証 |
| `tests/test_cloud_host.py` | 接続先サーバの解決、送信用接続からの受信（`CloudReceiver`：複数メッセージ・接続直後のまとめ送りの読み捨て） |
| `tests/test_dashboard.py` | サーバが寝ているときに起こして接続し直す |
| `tests/test_savefile.py` | 進行の保存と途中からの再開：全4チーム20問のどの瞬間でも読み戻した画面が一致する、チーム構成・問題・バルーンの食い違いを拒む、合言葉の引き継ぎ、続きの説明（アプリのダイアログ用） |

cloud通信層（`cloud.py`）の接続部分は実サーバ依存のためユニットテスト対象外。
変更したら上記スモークテスト＋E2Eで確認すること。**Scratch → Python の回答の経路は、本物の Scratch
（TurboWarp で開いた本番プロジェクト）か、生の WebSocket で `S2P_ANSWER` を書いて確かめる**（落とし穴7）。

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
呼ばないとプロセスが残る。**今は落とし穴7の理由で `cloud.events()` 自体を使っていない**
（受信は `CloudReceiver` のデーモンスレッドで行う）。新しく `cloud.events()` を使うコードを書くときは注意。

### 5. TurboWarp cloudの値は全員切断で消える

サーバはインメモリ。全クライアントが切断されると変数は消える。
Python側が常に正の状態を持ち、`resync` で再送できる設計を崩さないこと。

### 6. 送るだけの接続は1〜2分ごとに切られ、直後の送信が黙って消える

cloud-server は60秒ごとに ping を送り、次の ping までに pong を返さない接続を切る。
scratchattach の送信用接続（`TwCloud`）は**送るだけで何も読まない**ため、
websocket-client が pong を返す機会がなく、1〜2分ごとに切られる。
切られた直後の `set_var()` はエラーにならずに消え、そのあと自動で再接続される。

2026-10-04 の E2E（観客2人・全20問）で、**観客ページの進行が途中で止まる**形で再現した。
サーバログには `Timed out: no pong` が60秒おきに出ていた。

`CloudBridge` は送信用接続を読み続けるスレッド（`_start_pong_reader`）で pong を返している。
修正後は同じ E2E を最後まで通し、`no pong` が1回も出ないことを確認した。
**scratchattach を更新したり、接続の作り方を変えたりしたら、3分以上つないで
サーバログに `no pong` が出ないことを必ず確認する。**

### 7. scratchattach の受信は、自前サーバを指定しても公開サーバにつながる

`cloud.events()`（`CloudEvents`）は受信用に接続を作り直すが、`TwCloud` のときは
`cloud_host` を引き継がず、**TurboWarp の公開サーバ**（`wss://clouddata.turbowarp.org`）につながる
（scratchattach 2.2.1。`TwCloud` が `CustomCloud` のサブクラスでないため）。

そのため Render に切り替えた 2026-07-23 以降、**Scratch からの回答（`S2P_ANSWER`）は一度も Python に届いていなかった**。
エラーは出ない。2026-10-04 に本物の Scratch プロジェクトと結合して発見した。

`CloudBridge` は `cloud.events()` を使わず、送信用の接続を読む `CloudReceiver` で受信している
（落とし穴6の ping 応答も兼ねる）。修正後、TurboWarp で開いた本番の Scratch プロジェクトから
数字キーの回答が Python に届くことを確認した。**Scratch → Python の経路は、シミュレータではなく
本物の Scratch か、生の WebSocket で `S2P_ANSWER` を書いて確かめること**。

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
- [ ] Scratch担当に `docs/scratch/問題文リスト.txt` を取り込んでもらい、Scratch API で20問の一致を確かめる（[#50](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/50)。10/6 の実機確認では古いままだった）
- [ ] Scratch側で、チームの途中で開き直しても表示が崩れないよう直してもらい、実機で確かめる（[#52](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/52)）
- [x] ~~本番のルームID・接続先を裏方PCに設定~~ → `launcher/スズリーグ.app` が `1364239598`・`wss://suzuleague-cloud.onrender.com` で起動する（2026-10-05）。
      Scratch側の `?cloud_host=` と一致していることは 10/6 の実機確認で確認済み
- [x] ~~**Scratch側との結合テスト**~~ → **完了**。10/4 に自動操作、10/6 に実機で全体結果まで通した（#31）
- [ ] チーム名が確定したら `teams.json` をリポジトリ直下に作成（アプリの「本番」が読む。[#47](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/47)）
- [x] ~~**観客ページを本番room IDで再生成してデプロイ**~~ → **完了**（2026-08-06）。
      本番配信中のページが `1364239598` を向いていることを `curl` で実確認済み

      **問題を差し替えたときは再生成が必要**（問題文がページに埋め込まれているため）。
      手順は以下。

      ```bash
      uv run python -m suzuleague.audience --room-id 1364239598 \
        -o ../cloud-server/public/suzuleague.html
      ```

      cloud-server（`inouekoshi/cloud-server`）の `master` に push すると Render が
      自動デプロイする（実測で**push から約90秒**で反映）。デプロイ後は
      `curl -s https://suzuleague-cloud.onrender.com/suzuleague.html | grep 'var ROOM'`
      で `1364239598` になっていることを必ず確認する
- [ ] `uv run python -m suzuleague.publish --room-id 1364239598 --check` で、本番の画面が最新であることを確認
- [x] ~~観客ページを**実機のスマホ**で開いて表示を確認し、QRコードを発行~~ → **完了**（2026-10-06）
- [ ] QRコードを印刷する（`docs/qr/print.html`。クラス委員が掲げる）
- [ ] リハーサルの後、**スマホの**司会者画面の「ランキングをリセット」で観客ランキングを消す（PCの司会者画面にはこのボタンがない）
- [x] ~~Render 無料枠の残インスタンス時間を確認~~ → **0.08 / 750時間**（2026-10-04。#16）
- [ ] 司会者画面の合言葉を作り直す。Render の `HOST_TOKEN` を変え、裏方PCはアプリを開いて入れ直す（[#49](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/49)）
- [x] ~~司会のスマホで司会者画面を開き、「PCと接続中」になることを確認~~ → **完了**（2026-10-06）。合言葉を作り直したら司会のスマホで入れ直す
- [x] ~~出演者用の端末で数字キーの回答が司会者画面に届くことを確認~~ → **完了**（2026-10-06）
- [ ] 会場ネットワークでE2Eリハーサル（企画書のリハーサル項目参照）

### 当日（開演前）

- [ ] **開演30分前** に <https://suzuleague-cloud.onrender.com> をブラウザで開く
      （15分無通信でスピンダウンする。実測での復帰は22.8秒、公称は約1分）
- [ ] 裏方PCの**スリープを切り**、電源につなぐ（止まってもアプリの「続きから」で再開できるが、止まらないに越したことはない）
- [ ] `launcher/スズリーグ.app` の「本番」で、**「最初から」を選んで**ダッシュボードを起動して接続を確立する（以降 `HEARTBEAT` が15秒毎に流れるので眠らない）
- [ ] 司会のスマホの司会者画面が「PCと接続中」になっていることを確認
- [ ] ステージ画面を `?cloud_host=` 付きURLで開いて緑の旗を押し、司会者画面の「Scratch に今の状態を送り直す」で「開始までお待ちください」になることを確認
- [ ] Scratch の端末の**スリープ・自動ロックを切る**。チームの途中で開き直すと、次のチームまで表示が崩れる（[#52](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/52)）
- [ ] 進行不能時の代替手段を確認（司会者画面の「代理入力」、予備の回答画面 `/player.html`、
      「Scratch に今の状態を送り直す」、PCの司会者画面、[バックアップ手順](#本番サーバが落ちたときの代替手段)）

## プロジェクトの経緯・意思決定の記録

- 設計判断の理由: [architecture.md](./architecture.md#主要な設計判断とその理由)
- 通信は cloud 変数、本番は**セルフホストのサーバ**（2026-07-23 デプロイ済み）
- ~~UIは**CLIのまま本番へ**。Web UIは作らない~~ → 2026-10 デモ会の指摘を受けて、**司会がスマホで操作する司会者画面を追加**。CLIは予備として残す
- **観客スマホ参加は本番スコープに含む**（企画書・司会台本に組み込み済みのため。
  当初は次フェーズ送りとしていたが2026-07-22に格上げ）。回答は端末内で自己採点し
  サーバへ送らない方式（2026-07-23決定）
- **費用は0円**。予算枠がないため経費申請もしない（2026-07-23決定）
- **本番まで依存を更新しない**（2026-07-23決定）。Renovateも導入しない
- 期限: 本番は2026/10/31〜11/1だが、8月中の完成を目指す
- 技術的な詰まり・仕様変更はDiscordで即時相談する運用（上司指示）

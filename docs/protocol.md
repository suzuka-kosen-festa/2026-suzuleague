# スズリーグ Scratch⇔Python 通信仕様書

Scratch担当向けの共有仕様です。バックエンド（Python/scratchattach）とScratchプロジェクトは
**TurboWarpのクラウド変数**でやり取りします。

> **2026-10-04 追記**: 大画面は使わず、**Scratch 画面は出演者の手元の端末だけで使う**ことになった（#41）。
> 出演者の回答はこれまでどおり Scratch から `☁ S2P_ANSWER` で送る。司会の操作と観客の成績は、
> クラウド変数ではなく **HTTPS API** で送る（[後述](#https-apiスマホの画面と裏方pc)）。
> クラウド変数の仕様（この文書の大半）は変わっていない。

> 全体像は [architecture.md](./architecture.md)、ステートの詳しい意味と遷移図は
> [game-rules.md](./game-rules.md) を参照。

## 前提

- プロジェクトは **TurboWarp** で実行する
  - 観客・出演者はScratchアカウント不要（本家scratch.mit.eduのクラウド変数はログイン必須のため使わない）
- クラウド変数に入れられるのは**数値のみ**
- TurboWarpのクラウド変数は**全員が切断すると値が消える**
  - Python側に `resync`（全状態の再送）機能があり、Scratch側をリロードしたら司会者画面の「困ったとき」→「Scratch に今の状態を送り直す」で今の状態を送り直せる（CLIの `resync` と同じ）
  - **Scratch側は、途中の段階（例: チームの3問目の正解発表）をいきなり受けても画面を組み立てられるように作ってください。** 問題番号は `P2S_ROUND`、残りバルーンは `P2S_BALLOONS` から決め、それまでの演出を見ていた前提にしないこと。今の実装はここが未対応で、チームの途中で開き直すと次のチームまで表示が崩れる（[#52](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/52)）
- ルームID: 開発中は `suzuleague-dev`、**本番は `1364239598`**
  （Scratch担当のプロジェクト <https://scratch.mit.edu/projects/1364239598/> のID。
  Python側は環境変数 `SUZULEAGUE_PROJECT_ID` で切替可能）

### ⚠ URLに `?cloud_host=` を必ず付ける

本番では TurboWarp の公開サーバではなく、**このイベント専用に立てたサーバ**を使う。
プロジェクトを開くURLは次の形になる。

```
https://turbowarp.org/<プロジェクトID>?cloud_host=wss://suzuleague-cloud.onrender.com
```

**これを付け忘れると、何のエラーも出ないまま通信できない。** Scratch側は公開サーバ、
Python側は専用サーバという別々の部屋に入ってしまい、互いの変数が見えなくなる。
画面上は普通に動いているように見えるので、原因に気づくまで時間を溶かす。

専用サーバに切り替えた理由は、公開サーバに**1部屋128人の上限**があり、
観客のスマホ参加分を足すと足りないため（[詳細](./architecture.md#同時接続数の上限とセルフホスト方針)）。

## 変数一覧

### Python → Scratch（Scratch側は読むだけ。書き込まないこと）

| クラウド変数 | 内容 |
|---|---|
| `☁ P2S_SEQ` | 更新カウンタ。**Scratchはこの変数の変化だけを監視**し、変化したら他の変数を読む |
| `☁ P2S_STATE` | 進行ステート（下表） |
| `☁ P2S_TEAM` | 現在のチーム番号 (1〜4、開始前は0) |
| `☁ P2S_ROUND` | 現在のラウンド (1〜5、開始前は0) |
| `☁ P2S_QID` | 問題ID。Scratch側の問題文リストの**行番号**に対応 |
| `☁ P2S_CORRECT` | 正解パーセント。**非公開のあいだは -1**（正解発表・ラウンド結果のときだけ実際の値になる） |
| `☁ P2S_BALLOONS` | 現在チームのバルーン残数 |
| `☁ HEARTBEAT` | Python生存確認。約15秒ごとに更新される（接続中インジケータなどに利用可） |

> 送信順序について: 1回の状態更新では `P2S_SEQ` が**最後に**送信されます。
> そのため「SEQが変わった時点で他の変数は更新済み」と考えてOKです。

### Scratch → Python（Python側は読むだけ）

| クラウド変数 | 内容 |
|---|---|
| `☁ S2P_SEQ` | Scratch側の送信カウンタ（回答送信のたびに+1） |
| `☁ S2P_ANSWER` | 回答されたパーセント (0〜100の整数) |
| `☁ S2P_ACK` | 演出完了などの通知。`1` = バルーン割れアニメーション完了 |

回答送信時は `S2P_ANSWER` → `S2P_SEQ` の順に書き込んでください。

## 進行ステート (`☁ P2S_STATE`)

| 値 | ステート | Scratch側の表示イメージ |
|---|---|---|
| 0 | 待機 | タイトル画面 |
| 1 | チーム紹介 | チーム名・登場演出 |
| 2 | 出題 | 問題文表示（`P2S_QID` の問題） |
| 3 | 回答受付 | シンキングタイム演出・回答入力UI |
| 4 | 正解発表 | `P2S_CORRECT` を表示、バルーン割れアニメーション |
| 5 | ラウンド結果 | バルーン残数表示 |
| 6 | チーム結果 | クリア（`P2S_BALLOONS` > 0）/ ゲームオーバー演出 |
| 7 | 回答受付（エキシビション） | 3と同じだが**採点なし**であることを表示 |
| 8 | 全体結果 | 表彰式画面 |

- ステート遷移はすべて裏方PC（Python）が進めます。操作するのは司会者画面の「次へ」（またはCLIの `next`）です
- ゲームオーバー（バルーン0）になったチームの残り問題は、自動的にステート7（エキシビション）で出題されます

## 観客用クライアント

観客がスマホから参加する画面（`https://suzuleague-cloud.onrender.com/suzuleague.html`）も、
**ステージ画面とまったく同じ変数を受け取る**。
クラウド変数は同じ部屋の全クライアントへのブロードキャストなので、
裏方PCが状態を書き換えれば**ステージも観客も同じタイミングで次の問題に進む**。
同期のための追加の仕組みは要らない。

満たすべき条件は3つだけ。

1. ステージ画面と**同じ room ID** に繋ぐ
2. ステージ画面と**同じ cloud サーバ**に繋ぐ（`?cloud_host=wss://suzuleague-cloud.onrender.com`）
3. ステージ画面と同じく `☁ P2S_SEQ` の変化を検知して他の変数を読み直す

実測では150クライアントに配信しても端末間の差は数ミリ秒だった
（[architecture.md の進行の同期](./architecture.md#進行の同期)）。

### 観客側は S2P_* に書き込まない

**観客用の画面は `P2S_*` を読むだけ**で、送信するのは接続時の handshake のみ。
`☁ S2P_ANSWER` は挑戦者の回答を入れる場所なので、観客が書き込むと
**挑戦者の回答として採点されてしまう**。

観客の回答は**その端末の中で採点**する（正解発表時に届く `P2S_CORRECT` と
自分の回答の差でバルーンを減らす）。観客ランキングのための成績（誤差の合計）は、
クラウド変数ではなく HTTPS API（`/api/score`）で送る（[後述](#https-apiスマホの画面と裏方pc)）。

### 途中から参加した観客

cloud サーバは新規接続時に現在の変数値をまとめて送るため、
イベントの途中でQRコードを読んだ観客も、接続した瞬間に現在の問題から参加できる。

## HTTPS API（スマホの画面と裏方PC）

クラウド変数は誰でも書き込めて、値が全員に流れる。そのため「特定の人だけが送るもの」は
cloud サーバ（Render）の HTTPS API で受け付ける。実装は
[inouekoshi/cloud-server](https://github.com/inouekoshi/cloud-server) の `src/hostApi.js`・`src/rankingApi.js`。

| メソッド・パス | 誰が | 認証 | 内容 |
|---|---|---|---|
| `POST /api/host/command` | 司会者画面 | `X-Host-Token` | 操作を預ける。`{"type":"next","expect_state":2}` / `{"type":"answer","value":45}` |
| `GET /api/host/command?after=N` | 裏方PC | `X-Host-Token` | 預かった操作のうち `seq > N` のもの。`{"latest":N,"commands":[...]}` |
| `POST /api/host/state` | 裏方PC | `X-Host-Token` | 進行の状態を預ける（`host.py` の `build_host_state`） |
| `GET /api/host/state` | 司会者画面 | `X-Host-Token` | 状態と、裏方PCから最後に届いてからの秒数（`age`） |
| `GET /api/host/ranking` ・ `POST /api/host/ranking/hide` ・ `POST /api/host/ranking/reset` | 司会者画面 | `X-Host-Token` | 観客ランキングの一覧・名前の非表示・リセット |
| `POST /api/player/answer` | 出演者の回答画面 | 4桁の合言葉（本文の `code`） | `{"code":"4821","value":45,"question_id":7}`。回答受付中・同じ問題のときだけ受け付ける |
| `GET /api/player/state` | 回答画面・観客ページ | なし | 公開用の状態。**正解は発表後にしか含まない** |
| `POST /api/score` | 観客ページ | なし | `{"id","name","error_sum","answered","last_question"}` |
| `GET /api/ranking?id=&limit=` | 観客ページ・回答画面 | なし | 上位と参加人数、自分の順位 |

- `X-Host-Token` は Render の環境変数 `HOST_TOKEN` と裏方PCの `SUZULEAGUE_HOST_TOKEN` に同じ値を入れる。未設定ならAPIは503
- 出演者の合言葉は裏方PCがチームごとに発行し、`/api/host/state` の `player_code` で司会者画面に出す。
  間違いが1分に30回続くと30秒間すべて受け付けない
- 出演者の回答も、司会の「次へ」と同じ郵便受け（`/api/host/command` の取り出し）で裏方PCに届く。
  裏方PCは合言葉と問題番号をもう一度確かめてから回答として受け付ける

本番の出演者は Scratch 画面から `☁ S2P_ANSWER` で回答する。回答画面（`/api/player/answer`）は予備で、どちらから来ても同じ扱いになる。

## 問題文の扱い（ID参照方式）

クラウド変数は数値のみのため、問題文テキストは送りません。
Scratch側に問題文のリストを持ち、`☁ P2S_QID` を行番号としてリストから表示してください。

貼り付け用テキストはPython側から生成できます：

```
uv run python -m suzuleague.questions > questions.txt
```

（1行=1問、行番号=問題ID。Scratchエディタでリストを右クリック→「読み込み」）

## 動作確認の方法

Scratch側の実装がなくても、Python側だけで両側を動かして試せます：

```
# ターミナル1: 司会ダッシュボード
uv run suzuleague

# ターミナル2: Scratch側シミュレータ（動きの参考にどうぞ）
uv run python -m suzuleague.sim_scratch
```

Scratchプロジェクト側ができたら、TurboWarpで開いてダッシュボードと接続テストします。

## 技術的な注意（ハマりどころ）

- **1回のクラウド変数書き込みは1変数ずつ**行うこと。
  （参考: Python側ライブラリの一括送信機能はTurboWarpサーバに無視される不具合があり、
  1変数ずつ送る実装にしてある）
- `☁ P2S_CORRECT` は正解発表前は `-1`。Scratchの見た目に出さないよう注意
- **正解の数字はScratch側に持たせないこと。** Scratchプロジェクトは中身を誰でも
  見られるので、正解を埋め込むと当日ネタバレしうる。正解はPython側から
  発表のタイミングで `P2S_CORRECT` として届く
- 公開サーバ（`?cloud_host=` なし）には同一IPからの接続頻度制限があり、
  短時間に大量のリロードを繰り返すと一時的に接続できなくなる。
  **専用サーバにはこの制限はない**（cloud-server本体に実装がないことをソースで確認済み）

## 繋いだときに出やすい不具合

Python側とScratch側を初めて繋ぐときは、**エラーが出ないまま動かない**種類の
不具合が起きやすい。詰まったら次を疑ってほしい。

| 症状 | 疑うところ |
|---|---|
| 変数がまったく届かない | `?cloud_host=` の付け忘れ（上記）。room ID の食い違い |
| 一部の変数だけ届かない | **変数名の綴り・大文字小文字**。クラウド変数は名前が違うと黙って無視される |
| たまに表示が飛ぶ | `P2S_SEQ` の変化を見ずに個別の変数を監視していないか |
| 回答がPython側に届かない | `S2P_ANSWER` → `S2P_SEQ` の**順序**。SEQを先に書くと取りこぼす |
| 数値がおかしい | Scratch側から文字列として送られていないか |

裏方PCのダッシュボードには受信した回答が表示されるので、
**どちらまで届いているかは裏方PCの画面で切り分けられる**。

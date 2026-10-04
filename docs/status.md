# 進捗状況（2026-10-04 夜 時点）

このドキュメントは**今どこまで進んでいて、次に何をすべきか**を1枚で把握するためのもの。
設計の説明は [architecture.md](./architecture.md)、開発手順は [development.md](./development.md)、
**誰がどの画面を使うか**は [users.md](./users.md) を参照。

課題の全量と最新状況は
[GitHub Issues](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues)
と[マイルストーン](https://github.com/suzuka-kosen-festa/2026-suzuleague/milestones)
で管理している。このドキュメントは節目ごとの棚卸しとして更新する。

技術的な背景を知らない人には
[しくみの図解](https://htmlpreview.github.io/?https://github.com/suzuka-kosen-festa/2026-suzuleague/blob/main/docs/explainer.html)
（[docs/explainer.html](./explainer.html)）を先に渡すとよい。

## スケジュール

| マイルストーン | 期日 | 中身 |
|---|---|---|
| [M1 10/7デモ](https://github.com/suzuka-kosen-festa/2026-suzuleague/milestone/2) | **10/7（水）** | デモ会で指摘された画面をそろえて見せる |
| [M2 デモの指摘を反映](https://github.com/suzuka-kosen-festa/2026-suzuleague/milestone/3) | 10/17 | 指摘への対応・観客ランキングの負荷検証 |
| [M3 会場リハーサル](https://github.com/suzuka-kosen-festa/2026-suzuleague/milestone/4) | 10/24目安（日程未定） | 会場Wi-Fi・実機スマホ（観客・出演者・司会）で確認 |
| [M4 本番](https://github.com/suzuka-kosen-festa/2026-suzuleague/milestone/5) | 10/31（土）〜11/1（日） | **10/28にコード凍結**。以降は設定とデータの差し替えだけ |

イベント時間は40分、準備10分。参加者は4チーム・計15人。

当初の「8月末完成」は、Scratch側の実装を待つ形になって守れなかった。マイルストーンは閉じてある。

## 今の状況

**10/7デモで見せる画面は、すべて本番（Render）に出ている。** 残りは実機での確認（Scratch画面との結合を含む）。

デモ会（10月上旬）で「足りない」と言われた5つへの対応:

| 指摘 | 状態 | 画面・Issue |
|---|---|---|
| 視聴者参加画面 | ✅ 公開済み | 観客ページ <https://suzuleague-cloud.onrender.com/suzuleague.html> |
| スマホ用のUI | ✅ **全画面をスマホ縦画面で作った** | 観客ページ・司会者画面・出演者の回答画面 |
| ランキング機能 | ✅ 観客の中での順位（[#36](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/36)・[#37](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/37)・[#38](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/38)） | 観客ページ・回答画面の全体結果。300台の同時送信まで実測（[#32](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/32)） |
| リザルト画面（景品の案内） | ✅ 全体結果を全員のスマホに出す（[#35](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/35)） | 優勝チーム・景品・チームの順位・観客ランキング |
| 司会者画面 | ✅ 司会がスマホで操作（[#33](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/33)） | <https://suzuleague-cloud.onrender.com/host.html>（合言葉が必要） |
| （方針変更で追加）出演者の回答 | ✅ **既存のScratch画面の数字キー**で回答（[#41](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/41)）。予備としてスマホの回答画面（[#42](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/42)） | Scratch: ステージに置く端末で `https://turbowarp.org/1364239598?cloud_host=wss://suzuleague-cloud.onrender.com` |

**大方針（2026-10-04）: プロジェクター・大画面は使わない。司会と観客は自分のスマホ、出演者はステージに置いた端末の Scratch 画面で見る。** 使う人と画面の対応は [users.md](./users.md)。

```
バックエンド中核（進行・採点・通信）  ████████████████████ 完了
通信基盤（セルフホストサーバ）        ████████████████████ 完了・稼働中（送信が消える不具合も修正済み）
ゲームルール・本番問題データ          ████████████████████ 完了（企画側の問題表に差し替え済み）
司会者画面（スマホ）                  ██████████████████░░ 本番に公開済み・実機確認待ち
出演者の回答画面（スマホ・予備）      ██████████████████░░ 本番に公開済み
観客ページ・ランキング・リザルト      ██████████████████░░ 本番に公開済み・実機確認待ち
Scratch画面（出演者用）               ████████████████░░░░ 出演者の端末で使うと決定・実機での結合確認待ち
当日の運営体制                        ████████░░░░░░░░░░░░ 司会が操作すると決定・リハ日程とチーム名が未定
```

### 仕組みの要点

```
司会のスマホ（司会者画面）──操作──▶ ┐
出演者のスマホ（回答画面）──回答──▶ ├ Render（cloud-server）◀──取りに行く・状態を預ける── 裏方PC（Python・進行の中心）
観客のスマホ（観客ページ）──成績──▶ ┘        │
            ◀──────── 進行の配信（クラウド変数 P2S_*）──────┘
```

- 裏方PCは Render を郵便受けにして、司会の操作・出演者の回答を取り出す（スマホから裏方PCへは直接つながない）
- 司会者画面は合言葉（`HOST_TOKEN`）、出演者の回答はチームごとの4桁の合言葉で守る
- 画面の書き出しと本番との照合は `uv run python -m suzuleague.publish`（[手順](./development.md#観客用ページを更新する)）

### 今日（10/4）見つけて直した不具合

| 不具合 | 影響 | 対応 |
|---|---|---|
| Python の送信用接続が1〜2分ごとにサーバに切られ、直後の送信が黙って消える | 観客ページ（と Scratch 画面）の進行が途中で止まる | 接続を読み続けて ping に応答するよう修正。全20問のE2Eで再発なし（[落とし穴6](./development.md#6-送るだけの接続は12分ごとに切られ直後の送信が黙って消える)） |
| 観客ページで、2問目以降も1問目の回答のまま「決定済み」になる | 観客が2問目から答えられない | 問題ごとに回答を持つよう修正 |
| 観客ページで、正解発表とラウンド結果でバルーンが二重に減る | 観客の残りバルーンが実際より少なく出る | 問題ごとに1回だけ減らすよう修正 |

どれも7〜8月の検証では見つからなかったもので、**観客2人・全20問を通しで動かす E2E** で初めて出た。

## 残っていること

### M1：10/7 デモ

| Issue | 内容 | 担当 |
|---|---|---|
| [#31](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/31) | 実機での通し確認。**Scratch画面の数字キーで答えて、司会者画面に回答が届くか**も確かめる（段取り: [demo/README.md](./demo/README.md)） | 自分 |

### M2 以降

| マイルストーン | Issue | 内容 |
|---|---|---|
| M2 | [#45](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/45) | cloud-server を Org のリポジトリにまとめる（Org オーナーの承認が要る） |
| M2 | [#47](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/47) | チーム名と登場順を確定して `teams.json` を作る（イベント担当に確認） |
| M2 | [#48](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/48) | イベント担当に問題表の修正・アンケートの締め切り・台本への合言葉の一言を依頼 |
| M3 | [#21](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/21) | リハーサルの日程・会場Wi-Fiでの確認・裏方PCの置き場所 |
| M4 | [#49](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/49) | 本番前の最終準備（合言葉の作り直し・10/28 コード凍結・当日チェックリスト）。本番は10/31〜11/1 |

## 完了していること

### バックエンドの中核

| モジュール | 状態 |
|---|---|
| `engine.py` 進行ステートマシン・採点 | ✅ 9ステート実装。通信非依存でテスト可能 |
| `protocol.py` クラウド変数の規約 | ✅ P2S 7変数 + S2P 3変数を定義 |
| `cloud.py` cloud接続 | ✅ 状態push・回答受信・resync・heartbeat |
| `dashboard.py` 司会用CLI | ✅ next/answer/status/teams/resync（司会者画面の予備） |
| `controller.py` 進行操作の窓口 | ✅ CLI・司会者画面・Scratch・出演者の回答を同じロックで直列化 |
| `host.py` 司会者画面・回答画面の中継 | ✅ Render 経由の中継、出演者の合言葉、予備サーバ（`--web`） |
| `publish.py` 画面の書き出し | ✅ cloud-server への書き出しと本番との照合（`--check`） |
| `questions.py` 問題セット | ✅ 本番問題20問（アンケート2種の集計結果） |
| `sim_scratch.py` Scratch側シミュレータ | ✅ Scratch実装なしでE2E検証できる |
| `teams.py` チーム構成 | ✅ 名前・メンバー・登場順をJSONで差し替え可能（個人情報はリポジトリに置かない） |
| `audience.py` 観客用ページ | ✅ 公開済み。端末内で自己採点し、成績だけをランキングに送る |
| `loadtest.py` 負荷検証 | ✅ 接続数と配信到達率・遅延、観客ランキングの一斉送信（`--ranking`）を実測できる |
| テスト | ✅ Python 111件・cloud-server 53件パス |

### Scratch側との結合

本番プロジェクト [`1364239598`](https://scratch.mit.edu/projects/1364239598/)（2026-10-03更新）の中身を Scratch API で確認した（2026-10-04）。

- クラウド変数10個を実装済み。P2S 7変数・`S2P_SEQ`・`S2P_ANSWER`・`HEARTBEAT`
- `S2P_ACK` だけ未実装。Python側はログに出すだけなので、進行には影響しない
- 表示用リスト `問題文` 20件が `questions.py` と全件一致（[#5](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/5) クローズ）
- Scratch側の対応ステートは 1〜6。8（全体結果）の画面はない
- **出演者の手元の端末だけで使い、出演者は数字キーで回答する**と決定（2026-10-04、#41）

### 通信基盤

- 本番の接続先: **`wss://suzuleague-cloud.onrender.com`**（Render無料枠・Singapore・`MAX_CLIENTS=300`）。費用は0円
- 150接続まで全員に配信が届くことを実測済み（遅延約120ms）
- **無料枠の残量は 2026-10-04 時点で 0.08 / 750時間**。既存の2サービスはほとんど時間を使っておらず、10/31に枠切れで止まる心配はない（[#16](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/16) クローズ）
- サーバ停止時の代替手段（裏方PC上で動かして Cloudflare Quick Tunnel で公開）は実地で確認済み

詳細は [architecture.md](./architecture.md#同時接続数の上限とセルフホスト方針)。

### 確定したルール・方針

- **ぴったり賞は不採用**（2026-07-23）。本番では `--perfect-bonus` を付けない
- **誰が何問目を答えるかは司会がその場で決める**（2026-07-24）。システムは回答者を管理しない
- **サーバ費用は0円で組む**（2026-07-23）
- **本番まで依存を更新しない**（2026-07-23）。新機能も標準ライブラリで作る
- **プロジェクター・大画面は使わない**（2026-10-04）。司会と観客はスマホ、出演者はステージに置いた端末の **Scratch 画面**で見て数字キーで回答する（スマホの回答画面は予備）。司会者画面は司会がスマホで操作する。台本は紙で持ち、システムでは出さない
- **観客ランキングを作る**（2026-10 デモ会）。これに伴い「観客の回答はサーバへ送らない」方針を改め、成績だけを送る形にした（[#7](https://github.com/suzuka-kosen-festa/2026-suzuleague/issues/7) を引き継いでクローズ）

## 未解決の論点

| 論点 | 状況 |
|---|---|
| リハーサルの日程 | 企画書で未記入。会場Wi-Fiは会場でしか確かめられない（#21） |
| チーム名・登場順 | 企画書の「参加者から確認すること」。まだ仮の名前（#47） |
| 裏方PCの置き場所と回線 | 会場Wi-Fiかテザリングか。止まると全員の画面が止まる（#21） |
| 観客ランキングの上位に景品を出すか | 今は出さない前提。出すなら、自己申告のスコアでは不正を防げないので作り直しが要る |

## リスク

| リスク | 影響 | 対応 |
|---|---|---|
| **実機スマホでまだ誰も触っていない** | デモで操作に詰まる・表示が崩れる | iPhone サイズの WebKit で全画面を自動操作して確認済み。デモ前に実機で通す（[段取り](./demo/README.md)） |
| Render が落ちる・届かない | **全員の画面が止まる**（大画面がないので、スマホが唯一の画面） | 代替手段（裏方PC上でサーバを動かし Cloudflare Quick Tunnel で公開）は確認済み。司会者画面は裏方PC上でも開けるようにする |
| 観客のニックネームに不適切な語 | 全員のスマホのリザルトに出る | 既存のNGワードフィルタは英数字しか見ないので、日本語はすり抜ける。**司会者画面の「観客ランキング」から隠す**（#39） |
| 観客が出演者になりすまして回答する | 出演者の得点が狂う | チームごとに発行する4桁の合言葉がないと回答できないようにする（#42） |
| 正解の出る司会者画面を観客に見られる | 答えが漏れる | 正解は押したときだけ表示する |
| 司会者画面のURLが漏れて、他人に進行を操作される | 進行が乗っ取られる | 合言葉がないと操作できない。合言葉はRenderの環境変数と裏方PCにだけ置く（リポジトリには入れない）。本番前に作り直すとより安全 |
| リハーサルの観客ランキングが本番に残る | 本番の順位がおかしくなる | リハーサル後に司会者画面の「ランキングをリセット」を押す（チェックリストに記載） |
| Render に届かない（司会の操作だけ） | スマホから操作できない | 裏方PC上の同じ画面で操作を続ける（CLIも残す） |
| 会場ネットワークの品質 | 観客参加・ランキングが成立しない | 会場でのリハーサル（M3）で確認する |
| cloud サーバの単一障害点 | サーバが落ちると進行が止まる | 代替手段は確認済み（[手順](./development.md#本番サーバが落ちたときの代替手段)）。ランキングのデータはサーバ再起動で消えるが、イベント中は HEARTBEAT で起きている |
| Render 無料枠のスピンダウン（17分放置で復帰に22.8秒） | 開演直後にサーバが応答しない | **開演30分前に必ず起こす**。HEARTBEATを送り続けている間は眠らないことを実測済み |
| 観客数が `MAX_CLIENTS` を超える | 超過分の観客が静かに脱落する | 300に設定済み。配信は150接続、ランキングの一斉送信は300台まで実測で確認 |

## 運用ルール

- 技術的な詰まり・仕様変更は **Discord で即時相談**する
- 通信仕様を変えたら `protocol.py` と `docs/protocol.md` を同時に更新し、
  **Scratch担当に必ず連絡**する（[手順](./development.md#通信仕様を変更するとき)）
- 問題を差し替えたら、`questions.py`・Scratchの `問題文` リスト・観客ページ（`publish` で書き出してデプロイ）の**3か所を同時に更新**する
- **main（cloud-server は master）に直接コミットして push する**（2026-10-04〜）。cloud-server の push はそのまま本番デプロイなので、push 後は `publish --check` で照合する
- 本番投入前の確認事項は
  [リリースチェックリスト](./development.md#リリース本番投入チェックリスト) を参照

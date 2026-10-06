#!/bin/bash
# ダッシュボードをターミナルで起動する（「デモを起動.command」「本番を起動.command」から呼ばれる）
#
#   1. Render を起こし、合言葉が Render と一致するか確かめる
#   2. 本番の画面が最新か確かめる（publish --check）
#   3. ダッシュボードを起動し、裏方PCのブラウザで司会者画面を開く
#
# ふだんはターミナルを使わない「スズリーグ.app」を使う。こちらはコマンドも打てる予備。
# 合言葉は launcher/settings.env に保存する（初回に入力。Git には入らない）。
set -u

# shellcheck source=common.sh
source "$(dirname "$0")/common.sh"

# エラーで窓がすぐ閉じると読めないので、Enter を待ってから終える
pause_and_exit() {
  echo
  read -r -p "Enterキーで閉じます" _
  exit "${1:-1}"
}

cd "$REPO_DIR" || pause_and_exit

if ! select_teams "${1:-}"; then
  echo "使い方: launch.sh demo|production"
  pause_and_exit
fi

echo "=== スズリーグ ダッシュボード: $LABEL ==="
echo

if [ ! -f "$TEAMS" ]; then
  echo "✗ チーム構成 $TEAMS がありません。teams.example.json を元に作ってください"
  pause_and_exit
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "✗ uv が見つかりません。https://docs.astral.sh/uv/ からインストールしてください"
  pause_and_exit
fi

ask_token() {
  echo "司会者画面の合言葉（Render の HOST_TOKEN と同じもの）を入力してください。"
  read -r -s -p "合言葉: " SUZULEAGUE_HOST_TOKEN
  echo
  if [ -z "$SUZULEAGUE_HOST_TOKEN" ]; then
    echo "✗ 合言葉が空です"
    pause_and_exit
  fi
  save_token
  echo "→ launcher/settings.env に保存しました（次回から入力不要）"
  echo
}

load_token
if [ -z "$SUZULEAGUE_HOST_TOKEN" ]; then
  ask_token
fi

# 前に起動したダッシュボードが残っていると、予備の司会者画面のポートがぶつかって落ちる
if port_in_use; then
  echo "✗ ポート $WEB_PORT が使われています。"
  echo "  ダッシュボードがすでに起動していないか確認してください（前の窓で quit するか、スズリーグ.app で終了する）"
  pause_and_exit
fi

echo "[1/3] Render を起こして、合言葉を確かめています（寝ていると数十秒かかります）…"
if wake_render; then
  echo "  ✓ 応答あり"
else
  echo "  ⚠ 応答がありません。ネットにつながっているか確認してください（このまま続けます）"
fi

while :; do
  CODE=$(token_http_status)
  case "$CODE" in
    200)
      echo "  ✓ 合言葉が Render と一致しました"
      break
      ;;
    401)
      echo "  ✗ 合言葉が Render の HOST_TOKEN と違います。入れ直してください"
      ask_token
      ;;
    503)
      echo "  ✗ Render に HOST_TOKEN が設定されていません（管理画面の Environment で設定する）"
      pause_and_exit
      ;;
    *)
      echo "  ⚠ 合言葉を確かめられませんでした（HTTP $CODE）。このまま続けます"
      break
      ;;
  esac
done
echo

echo "[2/3] 本番の画面が最新か確かめています…"
if ! check_pages; then
  echo
  echo "⚠ 本番の画面が手元と違います。古い画面のまま進めると表示がずれることがあります"
  read -r -p "このまま起動するなら Enter（やめるならこの窓を閉じる）" _
fi
echo

# 前回の進行が残っていれば、続きから始めるか聞く（裏方PCを起動し直したとき。#51）
RESUME=""
INFO=$(resume_info)
case "$INFO" in
  none) ;;
  invalid:*)
    echo "⚠ 前回の進行を読めませんでした（${INFO#invalid:}）。最初から始めます"
    echo
    ;;
  *)
    echo "前回の続きがあります: $INFO"
    read -r -p "続きから始めますか？ [Y/n]（本番の開演前は n で最初から）: " ANSWER
    case "$ANSWER" in
      n|N|no|NO) echo "→ 最初から始めます（前回の進行は残しておきます）" ;;
      *) RESUME=resume; echo "→ 続きから始めます" ;;
    esac
    echo
    ;;
esac

echo "[3/3] ダッシュボードを起動します。終えるときは quit と打ってください"
echo

# 予備の司会者画面が立ち上がったら、裏方PCのブラウザで開く
# （ダブルクリックで開いたときだけ。パイプでの試験中にブラウザを出さない）
if [ -t 0 ]; then
  (
    for _ in $(seq 180); do
      if curl -s -o /dev/null "http://localhost:$WEB_PORT/host"; then
        open "http://localhost:$WEB_PORT/host"
        exit 0
      fi
      sleep 1
    done
  ) &
  OPENER_PID=$!
fi

set_dashboard_cmd "$RESUME"
"${DASHBOARD_CMD[@]}"
STATUS=$?

[ -n "${OPENER_PID:-}" ] && kill "$OPENER_PID" 2>/dev/null
pause_and_exit "$STATUS"

#!/bin/bash
# ダッシュボードをダブルクリックで起動する（「デモを起動.command」「本番を起動.command」から呼ばれる）
#
#   1. Render を起こし、合言葉が Render と一致するか確かめる
#   2. 本番の画面が最新か確かめる（publish --check）
#   3. ダッシュボードを起動し、裏方PCのブラウザで司会者画面を開く
#
# 合言葉は launcher/settings.env に保存する（初回に入力。Git には入らない）。
set -u

MODE="${1:-}"
LAUNCHER_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$LAUNCHER_DIR")"
SETTINGS="$LAUNCHER_DIR/settings.env"

CLOUD_HOST="wss://suzuleague-cloud.onrender.com"
SERVER_URL="https://suzuleague-cloud.onrender.com"
ROOM_ID="1364239598"
WEB_PORT=8000

# Finder から開くとシェルの設定によっては uv が見つからないため、よくある置き場所を足しておく
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# エラーで窓がすぐ閉じると読めないので、Enter を待ってから終える
pause_and_exit() {
  echo
  read -r -p "Enterキーで閉じます" _
  exit "${1:-1}"
}

cd "$REPO_DIR" || pause_and_exit

case "$MODE" in
  demo)
    TEAMS="docs/demo/teams-demo.json"
    LABEL="デモ（1チーム5問）"
    ;;
  production)
    TEAMS="teams.json"
    LABEL="本番（teams.json）"
    ;;
  *)
    echo "使い方: launch.sh demo|production"
    pause_and_exit
    ;;
esac

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

# 合言葉（Render の HOST_TOKEN と同じ値）。初回だけ入力して保存する
ask_token() {
  echo "司会者画面の合言葉（Render の HOST_TOKEN と同じもの）を入力してください。"
  read -r -s -p "合言葉: " SUZULEAGUE_HOST_TOKEN
  echo
  if [ -z "$SUZULEAGUE_HOST_TOKEN" ]; then
    echo "✗ 合言葉が空です"
    pause_and_exit
  fi
  (umask 077 && printf 'SUZULEAGUE_HOST_TOKEN=%q\n' "$SUZULEAGUE_HOST_TOKEN" > "$SETTINGS")
  echo "→ launcher/settings.env に保存しました（次回から入力不要）"
  echo
}

if [ -f "$SETTINGS" ]; then
  # shellcheck source=/dev/null
  source "$SETTINGS"
fi
if [ -z "${SUZULEAGUE_HOST_TOKEN:-}" ]; then
  ask_token
fi

# 前に起動したダッシュボードが残っていると、予備の司会者画面のポートがぶつかって落ちる
if lsof -iTCP:"$WEB_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "✗ ポート $WEB_PORT が使われています。"
  echo "  ダッシュボードがすでに起動していないか確認してください（前の窓で quit してから開き直す）"
  pause_and_exit
fi

echo "[1/3] Render を起こして、合言葉を確かめています（寝ていると数十秒かかります）…"
if curl -s -o /dev/null --max-time 90 "$SERVER_URL/"; then
  echo "  ✓ 応答あり"
else
  echo "  ⚠ 応答がありません。ネットにつながっているか確認してください（このまま続けます）"
fi

# 合言葉が Render と違うと、スマホの司会者画面が「PCがまだつながっていません」のままになる。
# 起動前に Render に聞いて確かめ、違えば入れ直してもらう
while :; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
    -H "X-Host-Token: $SUZULEAGUE_HOST_TOKEN" "$SERVER_URL/api/host/state")
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
if ! uv run python -m suzuleague.publish --room-id "$ROOM_ID" --check; then
  echo
  echo "⚠ 本番の画面が手元と違います。古い画面のまま進めると表示がずれることがあります"
  read -r -p "このまま起動するなら Enter（やめるならこの窓を閉じる）" _
fi
echo

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

uv run suzuleague \
  --cloud-host "$CLOUD_HOST" \
  --project-id "$ROOM_ID" \
  --teams "$TEAMS" \
  --web --web-port "$WEB_PORT"
STATUS=$?

[ -n "${OPENER_PID:-}" ] && kill "$OPENER_PID" 2>/dev/null
pause_and_exit "$STATUS"

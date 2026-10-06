# launch.sh（ターミナル版）と app.sh（アプリ版）で共通の設定と関数。source して使う。
#
# 試験用に、本番と別のルーム・ポート・合言葉ファイルで動かせるよう環境変数で差し替えられる
# （SUZULEAGUE_LAUNCH_ROOM / _PORT / _SETTINGS / _RUN_DIR）。ふだんは何も設定しない。

LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$LAUNCHER_DIR")"
SETTINGS="${SUZULEAGUE_LAUNCH_SETTINGS:-$LAUNCHER_DIR/settings.env}"
RUN_DIR="${SUZULEAGUE_LAUNCH_RUN_DIR:-$LAUNCHER_DIR/run}"

CLOUD_HOST="wss://suzuleague-cloud.onrender.com"
SERVER_URL="https://suzuleague-cloud.onrender.com"
ROOM_ID="${SUZULEAGUE_LAUNCH_ROOM:-1364239598}"
WEB_PORT="${SUZULEAGUE_LAUNCH_PORT:-8000}"

# Finder やアプリから開くとシェルの設定が読まれず uv が見つからないため、よくある置き場所を足しておく
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# 引数のモードからチーム構成と進行の保存先を決める。知らないモードなら 1 を返す
# （デモと本番で保存先を分け、デモの続きで本番を始めてしまわないようにする）
select_teams() {
  SAVE_FILE="$RUN_DIR/game-$1.json"
  case "$1" in
    demo)
      TEAMS="docs/demo/teams-demo.json"
      LABEL="デモ（1チーム5問）"
      ;;
    production)
      TEAMS="teams.json"
      LABEL="本番（teams.json）"
      ;;
    *)
      return 1
      ;;
  esac
}

# 保存した合言葉を読む（Render の HOST_TOKEN と同じ値）
load_token() {
  if [ -f "$SETTINGS" ]; then
    # shellcheck source=/dev/null
    source "$SETTINGS"
  fi
  export SUZULEAGUE_HOST_TOKEN="${SUZULEAGUE_HOST_TOKEN:-}"
}

save_token() {
  (umask 077 && printf 'SUZULEAGUE_HOST_TOKEN=%q\n' "$SUZULEAGUE_HOST_TOKEN" > "$SETTINGS")
}

# 寝ている Render を起こす（無料枠は15分無通信で眠り、起動に数十秒かかる）
wake_render() {
  curl -s -o /dev/null --max-time 90 "$SERVER_URL/"
}

# 合言葉が Render と一致するか、HTTP ステータスで返す（200=一致 401=違う 503=Render 側が未設定）。
# 違う合言葉のまま起動すると、スマホの司会者画面が「PCがまだつながっていません」のままになる
token_http_status() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
    -H "X-Host-Token: $SUZULEAGUE_HOST_TOKEN" "$SERVER_URL/api/host/state"
}

port_in_use() {
  lsof -iTCP:"$WEB_PORT" -sTCP:LISTEN >/dev/null 2>&1
}

check_pages() {
  uv run python -m suzuleague.publish --room-id "$ROOM_ID" --check
}

# 前回の進行の続きを1行で返す: none / invalid:<理由> / 続きの説明（select_teams の後に呼ぶ）
resume_info() {
  uv run python -m suzuleague.savefile --describe "$SAVE_FILE" --teams "$TEAMS"
}

# ダッシュボードの起動コマンドを DASHBOARD_CMD に入れる（select_teams の後に呼ぶ）。
# 引数に resume を渡すと、保存した進行の続きから始める
set_dashboard_cmd() {
  DASHBOARD_CMD=(
    uv run suzuleague
    --cloud-host "$CLOUD_HOST"
    --project-id "$ROOM_ID"
    --teams "$TEAMS"
    --web --web-port "$WEB_PORT"
    --save-file "$SAVE_FILE"
  )
  if [ "${1:-}" = resume ]; then
    DASHBOARD_CMD+=(--resume)
  fi
}

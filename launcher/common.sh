# launch.sh（ターミナル版）と app.sh（アプリ版）と jetson.sh（Jetson の遠隔操作）で共通の設定と関数。source して使う。
# app.sh は Jetson の上でもそのまま動く（jetson.sh が SSH 越しに呼ぶ）。
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

# 進行役を動かす Jetson（~/.ssh/config の Host 名。Tailscale 経由）と、その上での置き場所（ホームからの相対パス）。
# 2026-10-29 から、遠藤研究室の Jetson（Host jetson）をやめ、開発者の自宅の Jetson「john」（Host jetson-ts。個人の Tailscale）にした
JETSON_HOST="${SUZULEAGUE_JETSON_HOST:-jetson-ts}"
JETSON_DIR="${SUZULEAGUE_JETSON_DIR:-suzuleague}"

# Finder やアプリから開くとシェルの設定が読まれず uv が見つからないため、よくある置き場所を足しておく
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# 引数のモードからチーム構成と進行の保存先を決める。知らないモードなら 1 を返す
# （モードごとに保存先を分け、デモやリハーサルの続きで本番を始めてしまわないようにする）。
# リハーサルは本番のチームで、架空の問題を各チーム2問だけ出す（本番の問題を出演者に見せないため）
select_teams() {
  SAVE_FILE="$RUN_DIR/game-$1.json"
  REHEARSAL=""
  case "$1" in
    demo)
      TEAMS="docs/demo/teams-demo.json"
      LABEL="デモ（1チーム5問）"
      ;;
    rehearsal)
      TEAMS="teams.json"
      LABEL="リハーサル（架空の問題・各チーム2問）"
      REHEARSAL="--rehearsal"
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
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$WEB_PORT" -sTCP:LISTEN >/dev/null 2>&1
  else  # Jetson（Ubuntu）には lsof が入っていないことがある
    ss -Hltn "sport = :$WEB_PORT" 2>/dev/null | grep -q .
  fi
}

# Jetson で動いているあいだ、Jetson の進行の保存を数秒ごとにこの Mac へ写す（jetson.sh mirror）。
# Jetson は自宅に置いたまま使うので、Jetson が止まって SSH も届かなくなったときに、
# Mac で止まる前の場面から「続きから」始められるようにするため（2026-10-29〜）
MIRROR_PID_FILE="$RUN_DIR/mirror.pid"

mirror_pid() {
  local pid
  pid=$(cat "$MIRROR_PID_FILE" 2>/dev/null) || return 1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && echo "$pid"
}

# 写すのを止める。この Mac で起動する前に必ず呼ぶ（動き出した Mac の保存を、Jetson の保存で上書きしないため）。
# 写している側は、写し終えて置き換える直前に MIRROR_PID_FILE を見直すので、ファイルを消せば以降は置き換えない
stop_mirror() {
  local pid
  pid=$(mirror_pid) && kill "$pid" 2>/dev/null
  rm -f "$MIRROR_PID_FILE"
}

# どこかの裏方PCが最後に司会者画面の状態を預けてから何秒たったか（none=一度も預けていない、unknown=確かめられない）。
# 動いている裏方PCは2秒ごとに預けるので、数秒なら別の場所でまだ動いている。
# Mac と Jetson で同時に動かすと進行がぶつかるため、起動の前に確かめる
host_age() {
  curl -s --max-time 30 -H "X-Host-Token: $SUZULEAGUE_HOST_TOKEN" "$SERVER_URL/api/host/state" |
    uv run python -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(int(data["age"]) if data.get("state") else "none")
except (ValueError, KeyError, TypeError):
    print("unknown")
'
}

# 本番の画面が最新か確かめる。司会者画面・観客ページ・予備の回答画面（publish）に加えて、
# ステージ画面（stage）も見る。ステージ画面は、Scratch担当がプロジェクトを直したときと、
# teams.json のチーム名を変えたときに食い違う（どちらも作り直すまで本番に出ない）
check_pages() {
  local status=0
  uv run python -m suzuleague.publish --room-id "$ROOM_ID" --check || status=1
  uv run python -m suzuleague.stage --room-id "$ROOM_ID" --check || status=1
  return "$status"
}

# 前回の進行の続きを1行で返す: none / invalid:<理由> / 続きの説明（select_teams の後に呼ぶ）
resume_info() {
  uv run python -m suzuleague.savefile --describe "$SAVE_FILE" --teams "$TEAMS" $REHEARSAL
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
  if [ -n "$REHEARSAL" ]; then
    DASHBOARD_CMD+=("$REHEARSAL")
  fi
  if [ "${1:-}" = resume ]; then
    DASHBOARD_CMD+=(--resume)
  fi
}

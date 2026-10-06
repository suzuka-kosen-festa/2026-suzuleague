#!/bin/bash
# スズリーグ.app の裏側。アプリ（AppleScript）から1操作ずつ呼ばれ、結果を1行で返す。
# ダッシュボードはターミナルを開かずに裏で動かし、ログは launcher/run/dashboard.log に残す。
#
#   app.sh status             → running（このアプリで起動中）/ busy（ポートが他で使用中）/ stopped
#   app.sh check-token        → ok / missing / wrong / unset / unknown:<HTTPコード>（Render を起こしてから確かめる）
#   app.sh save-token         ← 標準入力の合言葉を保存する
#   app.sh check-pages        → 本番の画面が最新でなければ終了コード1
#   app.sh resume-info demo|production → none / invalid:<理由> / 前回の続きの説明
#   app.sh start demo|production [resume] → 予備の司会者画面が開けるまで待つ。失敗したらログの末尾を返す
#   app.sh stop               → 終了させる
set -u

# shellcheck source=common.sh
source "$(dirname "$0")/common.sh"

PID_FILE="$RUN_DIR/dashboard.pid"
LOG_FILE="$RUN_DIR/dashboard.log"

cd "$REPO_DIR" || exit 1

running_pid() {
  local pid
  pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && echo "$pid"
}

fail() {
  echo "$*" >&2
  exit 1
}

case "${1:-}" in
  status)
    if running_pid >/dev/null; then
      echo running
    elif port_in_use; then
      echo busy
    else
      echo stopped
    fi
    ;;

  check-token)
    load_token
    if [ -z "$SUZULEAGUE_HOST_TOKEN" ]; then
      echo missing
      exit 0
    fi
    wake_render
    case "$(token_http_status)" in
      200) echo ok ;;
      401) echo wrong ;;
      503) echo unset ;;
      *) echo "unknown:$(token_http_status)" ;;
    esac
    ;;

  save-token)
    IFS= read -r SUZULEAGUE_HOST_TOKEN || true
    [ -n "$SUZULEAGUE_HOST_TOKEN" ] || fail "合言葉が空です"
    save_token
    ;;

  check-pages)
    check_pages 2>&1
    ;;

  resume-info)
    select_teams "${2:-}" || fail "使い方: app.sh resume-info demo|production"
    [ -f "$TEAMS" ] || { echo none; exit 0; }
    resume_info
    ;;

  start)
    select_teams "${2:-}" || fail "使い方: app.sh start demo|production [resume]"
    [ -f "$TEAMS" ] || fail "チーム構成 $TEAMS がありません。teams.example.json を元に作ってください。"
    command -v uv >/dev/null 2>&1 || fail "uv が見つかりません。https://docs.astral.sh/uv/ からインストールしてください。"
    running_pid >/dev/null && fail "すでに起動しています。"
    port_in_use && fail "ポート $WEB_PORT が使われています。ターミナルで起動したダッシュボードが残っていれば、そちらで quit してください。"
    load_token

    mkdir -p "$RUN_DIR"
    [ -f "$LOG_FILE" ] && mv "$LOG_FILE" "$RUN_DIR/dashboard.prev.log"
    # 出力をすべてファイルへ向けて切り離す（つないだままだとアプリが終わるまで待ってしまう）
    set_dashboard_cmd "${3:-}"
    PYTHONUNBUFFERED=1 nohup "${DASHBOARD_CMD[@]}" --headless >"$LOG_FILE" 2>&1 </dev/null &
    echo $! >"$PID_FILE"

    # cloud サーバへの接続（寝ていれば起こしてやり直す）が済むと、予備の司会者画面が開く
    for _ in $(seq 150); do
      if curl -s -o /dev/null "http://localhost:$WEB_PORT/host"; then
        echo ok
        exit 0
      fi
      if ! running_pid >/dev/null; then
        rm -f "$PID_FILE"
        fail "起動に失敗しました。ログの最後:
$(tail -n 8 "$LOG_FILE")"
      fi
      sleep 1
    done
    fail "2分半待っても起動しませんでした。ネットにつながっているか確認してください。ログ: $LOG_FILE"
    ;;

  stop)
    pid=$(running_pid) || { rm -f "$PID_FILE"; echo ok; exit 0; }
    # uv run の下で Python が動いているので、子プロセスにも終了を伝える
    pkill -TERM -P "$pid" 2>/dev/null
    kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      pkill -KILL -P "$pid" 2>/dev/null
      kill -KILL "$pid" 2>/dev/null
    fi
    rm -f "$PID_FILE"
    echo ok
    ;;

  *)
    fail "使い方: app.sh status|check-token|save-token|check-pages|start|stop"
    ;;
esac

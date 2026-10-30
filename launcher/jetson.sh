#!/bin/bash
# Jetson で進行役（ダッシュボード）を動かすための、Mac 側の遠隔操作。スズリーグ.app から呼ばれる。
# Jetson には Tailscale 経由の SSH（~/.ssh/config の Host jetson-ts。接続先は common.sh の JETSON_HOST）で入り、Jetson 上の app.sh を呼ぶ。
#
#   jetson.sh status             → running / stopped / busy / not-setup（まだ送っていない）/ unreachable（SSH が届かない）
#   jetson.sh mode               → 起動中のモード（demo / rehearsal / production）。止まっていれば none
#   jetson.sh setup              → コード・チーム構成・合言葉を送り、uv と依存を入れる（初回とコードを直したとき）
#   jetson.sh resume-info demo|rehearsal|production → app.sh と同じ（Jetson に残っている進行の続き）
#   jetson.sh start demo|rehearsal|production [resume] → setup してから Jetson で起動する
#   jetson.sh stop               → Jetson のダッシュボードを終了させる
#   jetson.sh pull-save demo|rehearsal|production → Jetson の進行の保存を、この Mac に持ってくる（Mac に移すとき）
#   jetson.sh push-save demo|rehearsal|production → この Mac の進行の保存を、Jetson に送る（Jetson に移すとき）
#   jetson.sh tunnel             → Jetson の予備の司会者画面を、この Mac の http://localhost:8001/host で開けるようにする
#   jetson.sh untunnel           → tunnel を閉じる
#   jetson.sh mirror [demo|rehearsal|production] → Jetson の進行の保存を、3秒ごとにこの Mac の launcher/run/ へ写し続ける（裏で動く）
#   jetson.sh unmirror           → mirror を止める
#
# Mac と Jetson で同時に動かすと進行がぶつかるので、移すときは必ず片方を止めてから保存を移し、もう片方で「続きから」起動する。
# start は起動したあと mirror も始める。Jetson が止まって SSH も届かなくなっても、Mac には数秒前までの進行が残る。
# Mac で起動するとき（app.sh start）は mirror を止めてから起動する。
set -u

# shellcheck source=common.sh
source "$(dirname "$0")/common.sh"

TUNNEL_PORT="${SUZULEAGUE_JETSON_TUNNEL_PORT:-8001}"
MIRROR_INTERVAL="${SUZULEAGUE_JETSON_MIRROR_INTERVAL:-3}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15)

cd "$REPO_DIR" || exit 1

fail() {
  echo "$*" >&2
  exit 1
}

# Jetson でコマンドを実行する（終了コード 255 は SSH が届かなかったとき）
remote() {
  ssh "${SSH_OPTS[@]}" "$JETSON_HOST" "$@"
}

# Jetson 上の app.sh を呼ぶ。試験用のルーム（SUZULEAGUE_LAUNCH_ROOM）を指定していれば Jetson にも渡す
remote_app() {
  local env=""
  [ -n "${SUZULEAGUE_LAUNCH_ROOM:-}" ] && env="SUZULEAGUE_LAUNCH_ROOM=$(printf '%q' "$SUZULEAGUE_LAUNCH_ROOM") "
  remote "cd ~/$JETSON_DIR && ${env}launcher/app.sh $(printf '%q ' "$@")"
}

unreachable_msg() {
  echo "Jetson（$JETSON_HOST）に SSH でつながりません。Jetson の電源と、Mac・Jetson の Tailscale がオンになっているか確認してください。"
}

# モードの保存ファイル名（launcher/run からの相対）
save_name() {
  case "${1:-}" in
    demo | rehearsal | production) echo "game-$1.json" ;;
    *) fail "使い方: jetson.sh ${2:-pull-save} demo|rehearsal|production" ;;
  esac
}

setup() {
  remote true || fail "$(unreachable_msg)"
  remote "mkdir -p ~/$JETSON_DIR" || fail "Jetson に置き場所 ~/$JETSON_DIR を作れません。"
  # teams.json と合言葉（launcher/settings.env）は Git に入れていないので、ここで一緒に送る。
  # 進行の保存（launcher/run）は Jetson 側のものを残す（--delete でも除外したものは消えない）
  rsync -a --delete -e "ssh ${SSH_OPTS[*]}" \
    --exclude .git/ --exclude .venv/ --exclude launcher/run/ --exclude '__pycache__/' \
    --exclude .pytest_cache/ --exclude .ruff_cache/ --exclude .DS_Store --exclude '*.app/' \
    --exclude tools/stage-packager/node_modules/ --exclude tools/stage-packager/.assets/ \
    ./ "$JETSON_HOST:$JETSON_DIR/" || fail "Jetson にコードを送れませんでした。"
  # uv は Mac と同じ公式の手順で入れる。依存は uv.lock のとおりにそろえる（Python 3.12 も uv が入れる）
  remote "export PATH=\$HOME/.local/bin:\$PATH
    command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null || exit 1
    cd ~/$JETSON_DIR && uv sync --locked --quiet" 2>&1 || fail "Jetson で uv と依存を入れられませんでした。"
}

tunnel_pid() {
  pgrep -f "ssh.* -L $TUNNEL_PORT:localhost:$WEB_PORT $JETSON_HOST" | head -n 1
}

case "${1:-}" in
  status)
    out=$(remote "test -x ~/$JETSON_DIR/launcher/app.sh || { echo not-setup; exit 0; }
      cd ~/$JETSON_DIR && launcher/app.sh status" 2>/dev/null)
    code=$?
    if [ "$code" -eq 255 ]; then
      echo unreachable
    elif [ -n "$out" ]; then
      echo "$out"
    else
      echo unreachable
    fi
    ;;

  mode)
    remote_app mode || fail "$(unreachable_msg)"
    ;;

  setup)
    setup
    echo ok
    ;;

  resume-info)
    # 先にコードを送る。Jetson の app.sh が古いと、新しく足したモード（rehearsal など）を知らずに失敗する
    setup
    out=$(remote "cd ~/$JETSON_DIR && launcher/app.sh resume-info $(printf '%q' "${2:-}")" 2>&1)
    code=$?
    [ "$code" -eq 255 ] && fail "$(unreachable_msg)"
    [ "$code" -ne 0 ] && fail "Jetson で前回の続きを確かめられませんでした: $out"
    echo "$out"
    ;;

  start)
    save_name "${2:-}" start >/dev/null
    setup
    remote_app start "$2" ${3:+"$3"} || exit 1
    "$0" mirror "$2" >/dev/null
    ;;

  stop)
    remote_app stop || fail "$(unreachable_msg)"
    stop_mirror
    ;;

  pull-save)
    name=$(save_name "${2:-}" pull-save) || exit 1
    remote "test -f ~/$JETSON_DIR/launcher/run/$name"
    case $? in
      0) ;;
      255) fail "$(unreachable_msg)" ;;
      *) echo none; exit 0 ;;  # Jetson に進行の保存がない（まだ一度も操作していない）
    esac
    mkdir -p "$RUN_DIR"
    scp -q "${SSH_OPTS[@]}" "$JETSON_HOST:$JETSON_DIR/launcher/run/$name" "$RUN_DIR/$name.tmp" ||
      fail "Jetson から進行の保存を持ってこられませんでした。"
    # Mac に前からあった保存は消さずに残しておく
    [ -f "$RUN_DIR/$name" ] && mv "$RUN_DIR/$name" "$RUN_DIR/${name%.json}.before-pull.json"
    mv "$RUN_DIR/$name.tmp" "$RUN_DIR/$name"
    echo ok
    ;;

  push-save)
    name=$(save_name "${2:-}" push-save) || exit 1
    [ -f "$RUN_DIR/$name" ] || { echo none; exit 0; }
    remote "mkdir -p ~/$JETSON_DIR/launcher/run" || fail "$(unreachable_msg)"
    scp -q "${SSH_OPTS[@]}" "$RUN_DIR/$name" "$JETSON_HOST:$JETSON_DIR/launcher/run/$name.tmp" ||
      fail "Jetson に進行の保存を送れませんでした。"
    remote "cd ~/$JETSON_DIR/launcher/run && { [ ! -f $name ] || mv $name ${name%.json}.before-push.json; } && mv $name.tmp $name" ||
      fail "Jetson で進行の保存を置き換えられませんでした。"
    echo ok
    ;;

  tunnel)
    if [ -z "$(tunnel_pid)" ]; then
      ssh "${SSH_OPTS[@]}" -f -N -o ExitOnForwardFailure=yes \
        -L "$TUNNEL_PORT:localhost:$WEB_PORT" "$JETSON_HOST" ||
        fail "Jetson の予備の司会者画面につなげませんでした（ポート $TUNNEL_PORT が使われていないか確認してください）。"
    fi
    echo "http://localhost:$TUNNEL_PORT/host"
    ;;

  untunnel)
    pid=$(tunnel_pid)
    [ -n "$pid" ] && kill "$pid"
    echo ok
    ;;

  mirror)
    if mirror_pid >/dev/null; then
      echo ok
      exit 0
    fi
    mode="${2:-}"
    if [ -z "$mode" ]; then
      mode=$(remote_app mode) || fail "$(unreachable_msg)"
      [ "$mode" = none ] && { echo none; exit 0; }  # Jetson で動いていない
    fi
    name=$(save_name "$mode" mirror) || exit 1
    mkdir -p "$RUN_DIR"
    # Mac に前からあった保存は、写し始める前に1つだけ残しておく
    [ -f "$RUN_DIR/$name" ] && mv "$RUN_DIR/$name" "$RUN_DIR/${name%.json}.before-pull.json"
    nohup "$0" mirror-loop "$name" >/dev/null 2>&1 </dev/null &
    # 裏の処理が MIRROR_PID_FILE を書くまで待つ（すぐにもう一度 mirror を呼ばれても二重に始めない）
    for _ in $(seq 30); do
      mirror_pid >/dev/null && break
      sleep 0.1
    done
    echo ok
    ;;

  mirror-loop)  # mirror が裏で動かす。直接は呼ばない
    name="$2"
    echo $$ >"$MIRROR_PID_FILE"
    while [ "$(cat "$MIRROR_PID_FILE" 2>/dev/null)" = "$$" ]; do
      # Jetson は保存を別名で書いてから置き換えるので、書きかけを持ってくることはない
      if scp -q "${SSH_OPTS[@]}" "$JETSON_HOST:$JETSON_DIR/launcher/run/$name" "$RUN_DIR/$name.mirror" 2>/dev/null &&
        [ "$(cat "$MIRROR_PID_FILE" 2>/dev/null)" = "$$" ]; then
        mv "$RUN_DIR/$name.mirror" "$RUN_DIR/$name"
      fi
      sleep "$MIRROR_INTERVAL"
    done
    rm -f "$RUN_DIR/$name.mirror"
    ;;

  unmirror)
    stop_mirror
    echo ok
    ;;

  *)
    fail "使い方: jetson.sh status|mode|setup|resume-info|start|stop|pull-save|push-save|tunnel|untunnel|mirror|unmirror"
    ;;
esac

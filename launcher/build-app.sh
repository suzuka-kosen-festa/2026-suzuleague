#!/bin/bash
# app.applescript から スズリーグ.app を作り直す（app.applescript を書き換えたときに実行する）
set -eu
cd "$(dirname "$0")"
rm -rf "スズリーグ.app"
osacompile -o "スズリーグ.app" app.applescript
echo "作りました: launcher/スズリーグ.app"

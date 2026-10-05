#!/bin/bash
# ダブルクリックで、本番用（teams.json）のダッシュボードを起動する
exec "$(dirname "$0")/launch.sh" production

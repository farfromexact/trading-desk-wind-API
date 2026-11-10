#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python || ! -f frontend/dist/index.html ]]; then
  echo '请先运行 ./install-mac.command'
  exit 1
fi
echo '看板地址：http://127.0.0.1:8765'
echo '保持此窗口打开；按 Control+C 停止服务。首次连接若出现 Wind 登录框，请完成登录。'
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --no-access-log

#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# Finder's Terminal session can have a shorter PATH than an interactive shell.
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
if [[ -x .venv/bin/python ]]; then
  desk_python=.venv/bin/python
elif command -v python3.12 >/dev/null; then
  desk_python=python3.12
else
  desk_python=python3
fi
case "${1:-}" in
  '') set -- --open-browser ;;
  --no-browser) shift ;;
  *) echo '用法：双击此文件，或从终端加 --no-browser 启动。' >&2; exit 2 ;;
esac
exec "$desk_python" -X utf8 scripts/serve_dashboard_v2.py --build "$@"

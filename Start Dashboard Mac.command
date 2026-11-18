#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"
if [[ ! -x .venv/bin/python || ! -f frontend/dist/index.html ]]; then
  echo '请先运行 ./install-mac.command'
  exit 1
fi
exec .venv/bin/python -m scripts.mac_dashboards start "$@"

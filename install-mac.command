#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  "${PYTHON_BIN:-python3.12}" -m venv .venv
fi
.venv/bin/python scripts/configure_wind.py
.venv/bin/python -m pip install -r requirements-lock.txt
npm ci --no-audit --no-fund --prefix frontend
npm run build --prefix frontend
echo '安装完成。运行 .venv/bin/python wind_check.py 验证登录，然后双击 Start Dashboard Mac.command。'

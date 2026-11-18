#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m scripts.mac_dashboards stop "$@"

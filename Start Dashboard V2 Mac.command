#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# Compatibility for existing Finder shortcuts to the retired Mac V2 page.
exec './Start Dashboard Mac.command' "$@"

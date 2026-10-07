#!/usr/bin/env bash
# Run the local research node without Docker (macOS / Linux). Needs Python 3.11+.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
API="$HERE/../../apps/api"
if [ ! -f "$HERE/.env" ]; then
  echo "Create $HERE/.env from .env.example first." >&2
  exit 1
fi
if [ ! -d "$HERE/.venv" ]; then
  python3 -m venv "$HERE/.venv"
  "$HERE/.venv/bin/pip" install --upgrade pip
  "$HERE/.venv/bin/pip" install -e "$API[market_imports]"
fi
set -a
# shellcheck disable=SC1091
. "$HERE/.env"
set +a
cd "$API"
if [ "${1:-}" = "check" ]; then
  exec "$HERE/.venv/bin/python" scripts/check_nse_reachability.py
fi
exec "$HERE/.venv/bin/python" scripts/run_local_node.py "$@"

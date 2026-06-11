#!/usr/bin/env bash
# Runner for the market-sentiment engine. Keeps all logic out of SKILL.md.
# Ensures deps, then runs the Python CLI, passing through any flags.
set -euo pipefail

PROJECT="${MARKET_SENTIMENT_DIR:-$HOME/cody/market-sentiment}"
MAIN="$PROJECT/main.py"

if [[ ! -f "$MAIN" ]]; then
  echo "market-sentiment: project not found at $PROJECT" >&2
  echo "Set MARKET_SENTIMENT_DIR or clone the project there." >&2
  exit 1
fi

# Ensure the small dependency set is importable for the active python3.
if ! python3 -c "import requests, yaml" >/dev/null 2>&1; then
  echo "market-sentiment: installing deps (requests, PyYAML)..." >&2
  python3 -m pip install --quiet requests PyYAML >&2
fi

exec python3 "$MAIN" "$@"

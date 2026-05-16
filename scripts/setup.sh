#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! "$PYTHON_BIN" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
then
  echo "$PYTHON_BIN must be Python 3.9 or newer."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Check REPO_MAP_JSON before running."
else
  echo ".env already exists; left it unchanged."
fi

echo
echo "Setup complete. No venv or API keys are required."
echo "Run: ./scripts/run.sh once"


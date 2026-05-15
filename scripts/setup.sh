#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Check REPO_MAP_JSON before running."
else
  echo ".env already exists; left it unchanged."
fi

echo
echo "Setup complete."
echo "Run: . .venv/bin/activate"
echo "Then: emma-linear-codex-orchestrator once"

#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ -f frontend/package.json ] && [ ! -f frontend/dist/index.html ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "Dashboard frontend is not built and npm is missing. Run ./scripts/setup.sh first."
    exit 1
  fi
  echo "Building dashboard frontend..."
  npm --prefix frontend install
  npm --prefix frontend run build
fi
if [ "$#" -eq 0 ]; then
  set -- daemon
fi
PYTHONPATH=src "$PYTHON_BIN" -m linear_codex_orchestrator.main "$@"

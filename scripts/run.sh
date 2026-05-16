#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHONPATH=src "$PYTHON_BIN" -m linear_codex_orchestrator.main "$@"


#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"

have() {
  command -v "$1" >/dev/null 2>&1
}

install_apt() {
  if have sudo; then
    sudo apt-get update
    sudo apt-get install -y "$@"
  else
    apt-get update
    apt-get install -y "$@"
  fi
}

install_command() {
  local command_name="$1"
  local brew_package="$2"
  shift 2
  local apt_packages=("$@")

  if have "$command_name"; then
    return 0
  fi

  echo "Installing prerequisites because '$command_name' is missing..."
  if have brew; then
    brew install "$brew_package"
  elif have apt-get; then
    install_apt "${apt_packages[@]}"
  else
    echo "Could not install '$command_name' automatically. Install it, then rerun ./scripts/setup.sh."
    exit 1
  fi

  if ! have "$command_name"; then
    echo "Installed prerequisites, but '$command_name' is still not on PATH."
    exit 1
  fi
}

ensure_python() {
  install_command "$PYTHON_BIN" python python3

  if ! "$PYTHON_BIN" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
  then
    echo "$PYTHON_BIN must be Python 3.9 or newer."
    exit 1
  fi
}

ensure_npm() {
  if have npm; then
    return 0
  fi

  echo "Installing Node.js/npm because 'npm' is missing..."
  if have brew; then
    brew install node
  elif have apt-get; then
    install_apt nodejs npm
  else
    echo "Could not install npm automatically. Install Node.js/npm, then rerun ./scripts/setup.sh."
    exit 1
  fi

  if ! have npm; then
    echo "Installed Node.js/npm, but 'npm' is still not on PATH."
    exit 1
  fi
}

ensure_codex() {
  if have codex; then
    echo "Codex CLI found: $(command -v codex)"
    return 0
  fi

  ensure_npm

  echo "Installing Codex CLI globally..."
  npm install -g @openai/codex

  if ! have codex; then
    echo "Codex CLI install finished, but 'codex' is still not on PATH."
    exit 1
  fi

  echo "Codex CLI installed: $(command -v codex)"
}

check_auth() {
  local auth_ok=0
  local mcp_list

  echo
  echo "Checking authentication..."

  if mcp_list="$(codex mcp list 2>&1)"; then
    if ! grep -Eq '^linear[[:space:]]' <<<"$mcp_list"; then
      echo "Linear MCP is not configured."
      echo "Configure it in Codex, then confirm: codex mcp list"
      auth_ok=1
    elif grep -Eq '^linear[[:space:]].*Not logged in' <<<"$mcp_list"; then
      echo "Linear MCP is enabled, but not logged in."
      echo "Run: codex mcp login linear"
      auth_ok=1
    elif grep -Eq '^linear[[:space:]].*disabled' <<<"$mcp_list"; then
      echo "Linear MCP is configured, but disabled."
      echo "Enable Linear MCP, then confirm: codex mcp list"
      auth_ok=1
    else
      echo "Linear MCP is configured and authenticated."
    fi
  else
    echo "Codex is installed, but it is not logged in or MCP is not configured yet."
    echo "Run: codex --login"
    echo "Then configure Linear MCP and confirm: codex mcp list"
    auth_ok=1
  fi

  if gh auth status >/dev/null 2>&1; then
    echo "GitHub CLI is authenticated."
  else
    echo "GitHub CLI is installed, but not authenticated yet."
    echo "Run: gh auth login"
    auth_ok=1
  fi

  if [ "$auth_ok" -ne 0 ]; then
    echo
    echo "Setup installed prerequisites, but authentication is incomplete."
    exit 1
  fi
}

ensure_python
install_command git git git
install_command gh gh gh
ensure_codex

if [ -f frontend/package.json ]; then
  ensure_npm
  echo "Installing dashboard frontend dependencies..."
  npm --prefix frontend install
  echo "Building dashboard frontend..."
  npm --prefix frontend run build
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Check REPO_MAP_JSON before running."
else
  echo ".env already exists; left it unchanged."
fi

check_auth

echo
echo "Setup complete. No venv or .env API keys are required."
echo "Run: ./scripts/run.sh"

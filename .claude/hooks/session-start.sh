#!/bin/bash
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

if [ -f packages.txt ] && command -v apt-get >/dev/null 2>&1; then
  SUDO=""
  if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
  fi
  PACKAGES=$(grep -vE '^\s*(#|$)' packages.txt | tr '\n' ' ')
  if [ -n "$PACKAGES" ]; then
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get update -qq || true
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y --no-install-recommends $PACKAGES
  fi
fi

python3 -m pip install --break-system-packages -r requirements.txt

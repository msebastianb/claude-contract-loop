#!/usr/bin/env bash
# Installs the claude-contract-loop skill into Claude Code.
#   ./install.sh              # ${CLAUDE_HOME:-~/.claude}/skills/claude-contract-loop
#   ./install.sh --dir <path> # custom skills root
set -euo pipefail

SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/skills/claude-contract-loop"
ROOT="${CLAUDE_HOME:-$HOME/.claude}/skills"
if [ "${1:-}" = "--dir" ]; then
  [ -n "${2:-}" ] || { echo "error: --dir requires a path" >&2; exit 2; }
  ROOT="$2"
fi

DEST="$ROOT/claude-contract-loop"
mkdir -p "$ROOT"
rm -rf "$DEST"
cp -R "$SRC" "$DEST"
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} +
echo "installed: $DEST"

#!/usr/bin/env bash
# Redirect to the new install scripts under scripts/
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "Use the new install scripts under scripts/:"
echo "  bash ${SCRIPT_DIR}/scripts/mcp-install-for-opencode.sh ~/.config/opencode"
echo "  bash ${SCRIPT_DIR}/scripts/mcp-install-for-aionui.sh"
echo ""
exec bash "${SCRIPT_DIR}/scripts/mcp-install-for-opencode.sh" "$@"

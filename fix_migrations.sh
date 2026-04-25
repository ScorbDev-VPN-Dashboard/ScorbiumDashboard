#!/usr/bin/env bash
# =============================================================================
#  Fix Alembic Migration Conflicts — Auto-detect & repair
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
info()  { echo -e "${GREEN}[INFO]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error() { echo -e "${RED}[ERR]${RESET}  $*"; exit 1; }

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║     Fix Alembic Migration Conflicts (Auto)                ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check docker
docker ps | grep -q vpn_app || error "Container vpn_app not running. Run: docker compose up -d"
docker ps | grep -q vpn_db  || error "Container vpn_db not running"

info "Running fix_alembic.py inside vpn_app..."
docker exec vpn_app uv run python fix_alembic.py || {
    error "fix_alembic.py failed. Check logs above."
}

echo ""
echo "Done. Restart app if needed:"
echo "  docker compose restart app"


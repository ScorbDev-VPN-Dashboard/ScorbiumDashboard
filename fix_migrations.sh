#!/usr/bin/env bash
# =============================================================================
#  Fix Alembic migration conflicts
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'
info()  { echo -e "${GREEN}[INFO]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error() { echo -e "${RED}[ERR]${RESET}  $*"; exit 1; }

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║     Fix Alembic Migration Conflicts                       ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

# Check docker
docker ps | grep -q vpn_app || error "Container vpn_app not running. Run: docker compose up -d"
docker ps | grep -q vpn_db  || error "Container vpn_db not running"

info "Current alembic revision:"
docker exec vpn_app uv run alembic current 2>/dev/null || warn "Could not get current revision"

info "Alembic history:"
docker exec vpn_app uv run alembic history --verbose 2>/dev/null || true

echo ""
echo "Choose fix method:"
echo "  1) Fresh DB — stamp initial + upgrade to head (DB is empty)"
echo "  2) Missing admins table — stamp c4d5e6f7a8b9 + upgrade head"
echo "  3) All tables exist — stamp d5e6f7a8b9c0 (mark as complete)"
echo "  4) Check DB tables first"
read -rp "Choice [1/2/3/4]: " CHOICE

case "$CHOICE" in
  1)
    info "Stamping initial revision..."
    docker exec vpn_app uv run alembic stamp 4d5f8377eff0
    info "Upgrading to head..."
    docker exec vpn_app uv run alembic upgrade head
    success "Migrations applied"
    ;;
  2)
    info "Stamping revision c4d5e6f7a8b9..."
    docker exec vpn_app uv run alembic stamp c4d5e6f7a8b9
    info "Upgrading to head..."
    docker exec vpn_app uv run alembic upgrade head
    success "Migrations applied"
    ;;
  3)
    info "Stamping head revision d5e6f7a8b9c0..."
    docker exec vpn_app uv run alembic stamp d5e6f7a8b9c0
    success "Database marked as up-to-date"
    ;;
  4)
    info "Tables in database:"
    docker exec vpn_db psql -U postgres -d vpnbot -c "\dt" 2>/dev/null || error "Cannot connect to DB"
    echo ""
    info "Alembic version table:"
    docker exec vpn_db psql -U postgres -d vpnbot -c "SELECT * FROM alembic_version;" 2>/dev/null || warn "No alembic_version table"
    ;;
  *)
    error "Invalid choice"
    ;;
esac

echo ""
echo "Done. Restart app if needed:"
echo "  docker compose restart app"

#!/usr/bin/env bash
# =============================================================================
#  VPN Dashboard — Update
# =============================================================================
set -euo pipefail
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERR]${RESET}  $*"; exit 1; }

# ── Проверки ──────────────────────────────────────────────────────────────────
[[ ! -f .env ]] && error ".env не найден. Запустите setup.sh сначала."
[[ ! -f docker-compose.prod.yml ]] && error "Запустите скрипт из корня проекта."

DOMAIN=$(grep "^DOMAIN=" .env | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | xargs)
HTTPS_PORT=$(grep "^HTTPS_PORT=" .env | cut -d= -f2- | xargs)
HTTPS_PORT=${HTTPS_PORT:-443}

[[ -z "$DOMAIN" || "$DOMAIN" == "localhost" ]] && error "DOMAIN не задан в .env (нужен продакшен-домен)"

info "Домен: ${DOMAIN}, HTTPS порт: ${HTTPS_PORT}"

# ── [1/4] git pull ────────────────────────────────────────────────────────────
info "[1/4] Обновляю код..."
git pull || error "git pull failed. Проверьте: git status"

# ── APP_VERSION из pyproject.toml ─────────────────────────────────────────────
NEW_VER=$(grep '^version' pyproject.toml 2>/dev/null | head -1 | sed 's/.*= *"\(.*\)"/\1/' || true)
if [[ -n "$NEW_VER" ]]; then
    if grep -q "^APP_VERSION=" .env; then
        # Кросс-платформенный sed (GNU Linux vs BSD macOS)
        if sed --version 2>/dev/null | grep -q GNU; then
            sed -i "s/^APP_VERSION=.*/APP_VERSION=${NEW_VER}/" .env
        else
            sed -i '' "s/^APP_VERSION=.*/APP_VERSION=${NEW_VER}/" .env
        fi
    else
        echo "APP_VERSION=${NEW_VER}" >> .env
    fi
    info "APP_VERSION → ${NEW_VER}"
fi

# ── [2/4] nginx.conf ──────────────────────────────────────────────────────────
info "[2/4] Генерирую nginx.conf (${DOMAIN}:${HTTPS_PORT})..."

CERT_PATH="nginx/ssl/live/${DOMAIN}/fullchain.pem"
[[ ! -f "$CERT_PATH" ]] && warn "SSL сертификат не найден: ${CERT_PATH}. Запустите: certbot certonly --standalone -d ${DOMAIN}"

# Redirect: при 443 не добавляем порт в URL
if [[ "$HTTPS_PORT" == "443" ]]; then
    REDIR='return 301 https://$host$request_uri;'
else
    REDIR="return 301 https://\$host:${HTTPS_PORT}\$request_uri;"
fi

cat > nginx/nginx.conf << NGINXEOF
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 20M;
    gzip on;
    gzip_vary on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    limit_req_zone \$binary_remote_addr zone=panel:10m   rate=30r/m;
    limit_req_zone \$binary_remote_addr zone=api:10m     rate=60r/m;
    limit_req_zone \$binary_remote_addr zone=webhook:10m rate=120r/m;

    upstream vpn_app {
        server app:8000;
        keepalive 32;
    }

    server {
        listen 80;
        server_name ${DOMAIN};
        location /.well-known/acme-challenge/ { root /var/www/certbot; }
        location / { ${REDIR} }
    }

    server {
        listen ${HTTPS_PORT} ssl;
        http2 on;
        server_name ${DOMAIN};

        ssl_certificate     /etc/nginx/ssl/live/${DOMAIN}/fullchain.pem;
        ssl_certificate_key /etc/nginx/ssl/live/${DOMAIN}/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
        ssl_prefer_server_ciphers off;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 1d;
        ssl_session_tickets off;

        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
        add_header X-Frame-Options SAMEORIGIN always;
        add_header X-Content-Type-Options nosniff always;

        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;
        proxy_next_upstream   error timeout http_502 http_503;
        proxy_next_upstream_tries 2;

        location /panel/ {
            limit_req zone=panel burst=20 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /app/ {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /api/ {
            limit_req zone=api burst=30 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /webhook/ {
            limit_req zone=webhook burst=50 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /static/ {
            proxy_pass http://vpn_app;
            proxy_set_header Host \$host;
            expires 7d;
            add_header Cache-Control "public, immutable";
        }
        location ~ ^/(docs|redoc|openapi\.json) {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location = / { return 301 /panel/; }
        location / {
            proxy_pass http://vpn_app;
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
    }
}
NGINXEOF
success "nginx.conf готов"

# ── [3/4] Пересобираем и запускаем ───────────────────────────────────────────
info "[3/4] Пересобираю app..."
docker compose -f docker-compose.prod.yml build app

info "Перезапускаю контейнеры..."
docker compose -f docker-compose.prod.yml up -d db app

info "Жду готовности app (макс 90 сек)..."
for i in $(seq 1 18); do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' vpn_app 2>/dev/null || echo "unknown")
    if [[ "$STATUS" == "healthy" ]]; then
        success "App готов"
        break
    fi
    if [[ $i -eq 18 ]]; then
        warn "App не стал healthy за 90 сек, продолжаю..."
        docker compose -f docker-compose.prod.yml logs app --tail=20
    fi
    sleep 5
done

# ── [4/4] Миграции ────────────────────────────────────────────────────────────
info "[4/4] Применяю миграции БД..."
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head
success "Миграции применены"

# ── Перезапускаем nginx ───────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml up -d nginx
sleep 3
NGINX_STATUS=$(docker inspect --format='{{.State.Status}}' vpn_nginx 2>/dev/null || echo "unknown")
if [[ "$NGINX_STATUS" == "running" ]]; then
    success "nginx запущен"
else
    warn "nginx не запустился, проверьте логи:"
    docker compose -f docker-compose.prod.yml logs nginx --tail=15
fi

# ── Итог ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}║  ✅  Обновление завершено                        ║${RESET}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
if [[ "$HTTPS_PORT" == "443" ]]; then
    echo -e "  🌐 Панель: ${CYAN}https://${DOMAIN}/panel/${RESET}"
else
    echo -e "  🌐 Панель: ${CYAN}https://${DOMAIN}:${HTTPS_PORT}/panel/${RESET}"
fi
echo -e "  Логи:  docker compose -f docker-compose.prod.yml logs -f app"
echo ""

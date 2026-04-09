#!/usr/bin/env bash
set -euo pipefail
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERR]${RESET}  $*"; exit 1; }

# ── Читаем .env ───────────────────────────────────────────────────────────────
[[ ! -f .env ]] && error ".env не найден. Запустите setup.sh сначала."
[[ ! -f "docker-compose.prod.yml" ]] && error "Запустите скрипт из корня проекта"

DOMAIN=$(grep "^DOMAIN=" .env | sed -E 's/^DOMAIN=//' | sed -E 's/[[:space:]]*#.*//' | xargs)
HTTPS_PORT=$(grep "^HTTPS_PORT=" .env | cut -d= -f2)
if [[ -z "$HTTPS_PORT" ]]; then
    HTTPS_PORT=443 
fi

[[ -z "$DOMAIN" || "$DOMAIN" == "localhost" ]] && error "DOMAIN не задан в .env"

info "Домен: ${DOMAIN}, HTTPS порт: ${HTTPS_PORT}"

# ── git pull ──────────────────────────────────────────────────────────────────
info "[1/4] Обновляю код..."
if ! git pull; then
    error "git pull failed. Есть локальные изменения? Проверьте: git status"
fi

# ── APP_VERSION ───────────────────────────────────────────────────────────────
NEW_VER=$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
if [[ -n "$NEW_VER" ]]; then
    if grep -q "^APP_VERSION=" .env; then
        sed -i "s/^APP_VERSION=.*/APP_VERSION=${NEW_VER}/" .env
    else
        echo "APP_VERSION=${NEW_VER}" >> .env
    fi
    info "APP_VERSION → ${NEW_VER}"
fi

# ── Генерируем nginx.conf ─────────────────────────────────────────────────────
info "[2/4] Генерирую nginx.conf (порт ${HTTPS_PORT})..."

# Проверяем что сертификат существует
CERT_PATH="nginx/ssl/live/${DOMAIN}/fullchain.pem"
if [[ ! -f "$CERT_PATH" ]]; then
    warn "SSL сертификат не найден: ${CERT_PATH}"
    warn "Запустите: certbot certonly --standalone -d ${DOMAIN}"
fi

cat > nginx/nginx.conf << NGINXEOF
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 20M;
    gzip on;
    gzip_types text/plain text/css application/json application/javascript;

    limit_req_zone \$binary_remote_addr zone=panel:10m rate=30r/m;
    limit_req_zone \$binary_remote_addr zone=api:10m rate=60r/m;
    limit_req_zone \$binary_remote_addr zone=webhook:10m rate=120r/m;

    upstream vpn_app {
        server app:8000;
        keepalive 32;
    }

    server {
        listen 80;
        server_name ${DOMAIN};
        location /.well-known/acme-challenge/ { root /var/www/certbot; }
        location / { return 301 https://\$host:${HTTPS_PORT}\$request_uri; }
    }

    server {
        listen ${HTTPS_PORT} ssl;
        http2 on;
        server_name ${DOMAIN};

        ssl_certificate /etc/nginx/ssl/live/${DOMAIN}/fullchain.pem;
        ssl_certificate_key /etc/nginx/ssl/live/${DOMAIN}/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 1d;

        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
        proxy_next_upstream error timeout http_502 http_503;
        proxy_next_upstream_tries 3;

        location /panel/ {
            limit_req zone=panel burst=20 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /app/ {
            proxy_pass http://vpn_app;
            proxy_set_header Host \$host;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /webhook/ {
            limit_req zone=webhook burst=50 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host \$host;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location /api/ {
            limit_req zone=api burst=30 nodelay;
            proxy_pass http://vpn_app;
            proxy_set_header Host \$host;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
        location = / { return 301 /panel/; }
        location / {
            proxy_pass http://vpn_app;
            proxy_set_header Host \$host;
            proxy_set_header X-Forwarded-Proto \$scheme;
        }
    }
}
NGINXEOF
success "nginx.conf готов"

# ── Пересобираем и запускаем ──────────────────────────────────────────────────
info "[3/4] Пересобираю app..."
docker compose -f docker-compose.prod.yml build app

info "Перезапускаю контейнеры..."
docker compose -f docker-compose.prod.yml up -d db app

# Ждём healthy
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

# ── Миграции ──────────────────────────────────────────────────────────────────
info "[4/4] Применяю миграции БД..."
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head
success "Миграции применены"

# ── Nginx ─────────────────────────────────────────────────────────────────────
docker compose -f docker-compose.prod.yml up -d nginx

# Проверяем что nginx запустился
sleep 3
NGINX_STATUS=$(docker inspect --format='{{.State.Status}}' vpn_nginx 2>/dev/null || echo "unknown")
if [[ "$NGINX_STATUS" == "running" ]]; then
    success "nginx запущен"
else
    warn "nginx не запустился, проверьте логи:"
    docker compose -f docker-compose.prod.yml logs nginx --tail=10
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

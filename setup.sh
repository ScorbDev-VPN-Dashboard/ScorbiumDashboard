#!/usr/bin/env bash
# =============================================================================
#  VPN Dashboard — Setup & Deploy
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET} $*"; }
success() { echo -e "${GREEN}[OK]${RESET}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERR]${RESET}  $*"; exit 1; }

echo -e "${BOLD}${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║       Scorbium Dashboard VPN  — Setup & Deploy            ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${RESET}"

# ── Зависимости ───────────────────────────────────────────────────────────────
for cmd in docker curl openssl; do
    command -v "$cmd" &>/dev/null || error "Не найден '$cmd'. Установите его."
done
docker compose version &>/dev/null || error "Нужен Docker Compose v2."

# ── Режим ─────────────────────────────────────────────────────────────────────
echo ""
echo "Режим запуска:"
echo "  1) Продакшен (домен + SSL) — только на VPS"
echo "  2) Разработка (localhost)"
read -rp "Выбор [1/2]: " MODE
MODE=${MODE:-2}

# ── Ввод данных ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Основные ────────────────────────────────────────${RESET}"
read -rp "Название панели По умолчание(Scorbium Dashboard VPN): " APP_NAME
APP_NAME=${APP_NAME:-"Scorbium Dashboard VPN"}

read -rp "Telegram Bot Token: " BOT_TOKEN
[[ -z "$BOT_TOKEN" ]] && error "Bot Token обязателен"

read -rp "Telegram Admin IDs (например: 123456789): " ADMIN_IDS_RAW
[[ -z "$ADMIN_IDS_RAW" ]] && error "Admin IDs обязательны"
ADMIN_IDS="[$(echo "$ADMIN_IDS_RAW" | tr -s ' ,' ',' | sed 's/^,//;s/,$//')]"

read -rp "Логин панели [admin]: " WEB_USER
WEB_USER=${WEB_USER:-admin}

read -rsp "Пароль панели (мин. 6 символов): " WEB_PASS
echo ""
# Generate a dedicated JWT secret (32 bytes, hex-encoded)
JWT_SECRET_KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(32))")
info "Сгенерирован JWT_SECRET_KEY"
echo ""
[[ ${#WEB_PASS} -lt 6 ]] && error "Пароль слишком короткий"

echo ""
echo -e "${BOLD}── База данных ─────────────────────────────────────${RESET}"
read -rp "Имя БД [vpnbot]: " DB_NAME; DB_NAME=${DB_NAME:-vpnbot}
read -rp "Пользователь БД [postgres]: " DB_USER; DB_USER=${DB_USER:-postgres}
read -rsp "Пароль БД [postgres]: " DB_PASS; echo ""; DB_PASS=${DB_PASS:-postgres}

echo ""
echo -e "${BOLD}── VPN Panel (Marzban / Pasarguard) ─────────────────${RESET}"
VPN_PANEL_TYPE=marzban
echo ""
read -rp "URL панели (например: https://panel.example.com:8012): " PASAR_URL
[[ -z "$PASAR_URL" ]] && error "URL панели обязателен"
read -rp "Логин Marzban [admin]: " PASAR_LOGIN; PASAR_LOGIN=${PASAR_LOGIN:-admin}
read -rsp "Пароль Marzban: " PASAR_PASS; echo ""
[[ -z "$PASAR_PASS" ]] && error "Пароль Marzban обязателен"
success "Выбрана панель: Marzban / Pasarguard"

echo ""
echo -e "${BOLD}── YooKassa и CryptoBot ────────────────────────────${RESET}"
echo -e "${YELLOW}Настройте платёжные системы через панель: Telegram → Платёжные системы${RESET}"

# ── Продакшен-специфичные ─────────────────────────────────────────────────────
if [[ "$MODE" == "1" ]]; then
    echo ""
    echo -e "${BOLD}── Домен и SSL ─────────────────────────────────────${RESET}"
    echo -e "${YELLOW}⚠️  Домен должен уже указывать A-записью на IP этого сервера!${RESET}"
    read -rp "Домен (без https://): " DOMAIN; [[ -z "$DOMAIN" ]] && error "Обязателен"
    read -rp "Email для Let's Encrypt: " LE_EMAIL; [[ -z "$LE_EMAIL" ]] && error "Обязателен"
    echo ""
    echo "HTTPS порт:"
    echo "  1) 443 (стандартный)"
    echo "  2) 8443 (альтернативный, если 443 занят)"
    read -rp "Выбор [1/2]: " PORT_CHOICE
    if [[ "$PORT_CHOICE" == "2" ]]; then
        HTTPS_PORT=8443
    else
        HTTPS_PORT=443
    fi
    TG_PROTOCOL=webhook
    if [[ "$HTTPS_PORT" == "443" ]]; then
        WEBHOOK_URL="https://${DOMAIN}/webhook/bot"
    else
        WEBHOOK_URL="https://${DOMAIN}:${HTTPS_PORT}/webhook/bot"
    fi
    ALLOWED_ORIGINS='["https://'"${DOMAIN}"'"]'
    if [[ "$HTTPS_PORT" == "443" ]]; then
        PANEL_URL="https://${DOMAIN}/panel/"
    else
        PANEL_URL="https://${DOMAIN}:${HTTPS_PORT}/panel/"
    fi
else
    DOMAIN="localhost"
    HTTPS_PORT=443
    TG_PROTOCOL=long
    WEBHOOK_URL="https://localhost/webhook/bot"
    ALLOWED_ORIGINS='["http://localhost:8000"]'
    PANEL_URL="http://localhost/panel/"
fi

# ── Генерация .env ────────────────────────────────────────────────────────────
info "Генерирую .env..."

# Читаем версию из pyproject.toml до heredoc
if [[ -f "pyproject.toml" ]]; then
    APP_VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
    [[ -z "$APP_VERSION" ]] && APP_VERSION="1.0.0"
else
    APP_VERSION="1.0.0"
    warn "pyproject.toml не найден, использую версию ${APP_VERSION}"
fi

cat > .env <<EOF
APP_NAME=${APP_NAME}
APP_VERSION=${APP_VERSION}
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
ALLOWED_ORIGINS=${ALLOWED_ORIGINS}
WEB_SUPERADMIN_USERNAME=${WEB_USER}
WEB_SUPERADMIN_PASSWORD=${WEB_PASS}
TELEGRAM_BOT_TOKEN=${BOT_TOKEN}
TELEGRAM_ADMIN_IDS=${ADMIN_IDS}
TELEGRAM_TYPE_PROTOCOL=${TG_PROTOCOL}
TELEGRAM_WEBHOOK_URL=${WEBHOOK_URL}
TELEGRAM_WEBHOOK_PATH=/webhook/bot
PASARGUARD_ADMIN_PANEL=${PASAR_URL}
PASARGUARD_ADMIN_LOGIN=${PASAR_LOGIN}
PASARGUARD_ADMIN_PASSWORD=${PASAR_PASS}
PASARGUARD_API_KEY=
VPN_PANEL_TYPE=marzban
JWT_SECRET_KEY=${JWT_SECRET_KEY}
HTTPS_PORT=${HTTPS_PORT}
DOMAIN=${DOMAIN}
DB_ENGINE=postgresql
DB_NAME=${DB_NAME}
DB_HOST=db
DB_PORT=5432
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASS}
LOG_PATH=logs
LOG_ROTATION=1 day
LOG_RETENTION=30 days
LOG_LEVEL=INFO
EOF
success ".env создан"

# ── Запуск ────────────────────────────────────────────────────────────────────
echo ""
read -rp "Запустить? [Y/n]: " START; START=${START:-Y}
[[ ! "$START" =~ ^[Yy]$ ]] && { info "Запустите вручную."; exit 0; }

if [[ "$MODE" == "1" ]]; then
    # ── ПРОДАКШЕН ─────────────────────────────────────────────────────────────

    # Настраиваем nginx.conf под домен и порт
    info "Генерирую nginx.conf для ${DOMAIN}:${HTTPS_PORT}..."

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
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self' wss: https:;" always;

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
        # WebSocket notifications endpoint
        location /ws/notifications {
            proxy_pass http://vpn_app;
            proxy_http_version 1.1;
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host              \$host;
            proxy_set_header X-Real-IP         \$remote_addr;
            proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto \$scheme;
            proxy_read_timeout 86400s;
            proxy_send_timeout 86400s;
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
    success "nginx.conf создан (${DOMAIN}:${HTTPS_PORT})"

    # Останавливаем старые контейнеры
    docker compose -f docker-compose.prod.yml down 2>/dev/null || true

    # Создаём нужные директории
    mkdir -p nginx/ssl certbot_www

    # Получаем SSL сертификат (до запуска nginx)
    CERT_PATH="nginx/ssl/live/${DOMAIN}/fullchain.pem"
    if [[ -f "$CERT_PATH" ]]; then
        success "SSL сертификат уже существует, пропускаю"
    else
        info "Получаю SSL сертификат через certbot standalone..."

        if ! command -v certbot &>/dev/null; then
            info "Устанавливаю certbot..."
            apt-get update -qq
            apt-get install -y -qq certbot
        fi

        # Certbot standalone — поднимает свой HTTP сервер на порту 80
        # nginx ещё не запущен, поэтому порт свободен
        certbot certonly --standalone \
            --email "${LE_EMAIL}" \
            --agree-tos \
            --no-eff-email \
            -d "${DOMAIN}" || {
            warn "Не удалось получить SSL сертификат."
            warn "Убедитесь что домен указывает на этот сервер и порт 80 открыт."
            warn "Запустите вручную: certbot certonly --standalone -d ${DOMAIN}"
            exit 1
        }

        # Копируем сертификаты в папку проекта (nginx монтирует ./nginx/ssl)
        info "Копирую сертификаты..."
        mkdir -p "nginx/ssl/live/${DOMAIN}"
        cp "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" "nginx/ssl/live/${DOMAIN}/"
        cp "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" "nginx/ssl/live/${DOMAIN}/"
        success "Сертификаты скопированы"

        # Настраиваем автообновление через cron
        PROJECT_DIR="$(pwd)"
        CRON_FILE="/etc/cron.d/vpn-certbot-renew"
        cat > "$CRON_FILE" <<CRONEOF
0 3 * * * root certbot renew --quiet --standalone \
  --pre-hook "docker compose -f ${PROJECT_DIR}/docker-compose.prod.yml stop nginx" \
  --post-hook "cp /etc/letsencrypt/live/${DOMAIN}/fullchain.pem ${PROJECT_DIR}/nginx/ssl/live/${DOMAIN}/ && cp /etc/letsencrypt/live/${DOMAIN}/privkey.pem ${PROJECT_DIR}/nginx/ssl/live/${DOMAIN}/ && docker compose -f ${PROJECT_DIR}/docker-compose.prod.yml start nginx"
CRONEOF
        chmod 644 "$CRON_FILE"
    # Ensure cron file ends with newline (required by cron)
    echo "" >> "$CRON_FILE"
        success "Автообновление сертификата настроено (каждый день в 3:00)"
    fi

    # Запускаем db + app
    info "Запускаю db и app..."
    docker compose -f docker-compose.prod.yml up -d db app

    # Ждём healthy
    info "Жду готовности app (макс 90 сек)..."
    for i in $(seq 1 18); do
        STATUS=$(docker inspect --format='{{.State.Health.Status}}' vpn_app 2>/dev/null || echo "starting")
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

    # Применяем миграции (auto-fix перед upgrade)
    info "Применяю миграции БД..."
    docker compose -f docker-compose.prod.yml exec app uv run python fix_alembic.py
    success "Миграции применены"

    # Запускаем nginx с SSL
    info "Запускаю nginx с SSL..."
    docker compose -f docker-compose.prod.yml up -d nginx

    # Проверяем nginx
    sleep 3
    NGINX_STATUS=$(docker inspect --format='{{.State.Status}}' vpn_nginx 2>/dev/null || echo "unknown")
    if [[ "$NGINX_STATUS" != "running" ]]; then
        warn "nginx не запустился, проверьте логи:"
        docker compose -f docker-compose.prod.yml logs nginx --tail=20
    else
        success "nginx запущен"
    fi

else
    # ── РАЗРАБОТКА ────────────────────────────────────────────────────────────
    info "Запускаю в режиме разработки..."
    docker compose down 2>/dev/null || true
    docker compose up -d db app nginx
    info "Жду запуска (15 сек)..."
    sleep 15
    info "Применяю миграции БД..."
    docker compose exec app uv run python fix_alembic.py
    success "Миграции применены"
fi

# ── Итог ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║           ✅  Готово!                            ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  🌐 Панель:   ${BOLD}${CYAN}${PANEL_URL}${RESET}"
echo -e "  👤 Логин:    ${BOLD}${WEB_USER}${RESET}"
echo -e "  🔑 Пароль:   ${BOLD}${WEB_PASS}${RESET}"
echo -e "  🛡️  VPN:      ${BOLD}Marzban / Pasarguard${RESET} (${PASAR_URL})"
echo ""
echo -e "  Логи:    ${YELLOW}docker compose -f docker-compose.prod.yml logs -f app${RESET}"
echo -e "  Стоп:    ${YELLOW}docker compose -f docker-compose.prod.yml down${RESET}"
echo -e "  Обновить: ${YELLOW}git pull && docker compose -f docker-compose.prod.yml build app && docker compose -f docker-compose.prod.yml up -d app${RESET}"
echo ""

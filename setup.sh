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
[[ ${#WEB_PASS} -lt 6 ]] && error "Пароль слишком короткий"

echo ""
echo -e "${BOLD}── База данных ─────────────────────────────────────${RESET}"
read -rp "Имя БД [vpnbot]: " DB_NAME; DB_NAME=${DB_NAME:-vpnbot}
read -rp "Пользователь БД [postgres]: " DB_USER; DB_USER=${DB_USER:-postgres}
read -rsp "Пароль БД [postgres]: " DB_PASS; echo ""; DB_PASS=${DB_PASS:-postgres}

echo ""
echo -e "${BOLD}── VPN Panel ────────────────────────────────────────${RESET}"
echo "Тип панели:"
echo "  1) Marzban / Pasarguard (по умолчанию)"
echo "  2) Remnawave"
read -rp "Выбор [1/2]: " PANEL_CHOICE
PANEL_CHOICE=${PANEL_CHOICE:-1}

if [[ "$PANEL_CHOICE" == "2" ]]; then
    VPN_PANEL_TYPE=remnawave
    read -rp "URL Remnawave (например: https://panel.example.com): " REMNAWAVE_URL
    [[ -z "$REMNAWAVE_URL" ]] && error "URL Remnawave обязателен"
    read -rp "Логин Remnawave [admin]: " REMNAWAVE_LOGIN; REMNAWAVE_LOGIN=${REMNAWAVE_LOGIN:-admin}
    read -rsp "Пароль Remnawave: " REMNAWAVE_PASS; echo ""
    PASAR_URL="http://localhost:8012"
    PASAR_LOGIN="admin"
    PASAR_PASS="unused"
else
    VPN_PANEL_TYPE=marzban
    REMNAWAVE_URL=""
    REMNAWAVE_LOGIN=""
    REMNAWAVE_PASS=""
    read -rp "URL панели (например: https://panel.example.com:8012): " PASAR_URL
    [[ -z "$PASAR_URL" ]] && error "URL панели обязателен"
    read -rp "Логин Marzban [admin]: " PASAR_LOGIN; PASAR_LOGIN=${PASAR_LOGIN:-admin}
    read -rsp "Пароль Marzban: " PASAR_PASS; echo ""
fi

echo ""
echo -e "${BOLD}── YooKassa (Enter = пропустить) ───────────────────${RESET}"
read -rp "Shop ID: " YK_SHOP; YK_SHOP=${YK_SHOP:-""}
read -rp "Secret Key: " YK_SECRET; YK_SECRET=${YK_SECRET:-""}

echo ""
echo -e "${BOLD}── CryptoBot (Enter = пропустить) ──────────────────${RESET}"
read -rp "CryptoBot API Token (@CryptoBot → /pay): " CRYPTOBOT_TOKEN; CRYPTOBOT_TOKEN=${CRYPTOBOT_TOKEN:-""}

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
    WEBHOOK_URL="https://${DOMAIN}/webhook/bot"
    ALLOWED_ORIGINS='["https://'"${DOMAIN}"'"]'
    PANEL_URL="https://${DOMAIN}${HTTPS_PORT:+:${HTTPS_PORT}}/panel/"
    [[ "$HTTPS_PORT" == "443" ]] && PANEL_URL="https://${DOMAIN}/panel/"
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
cat > .env <<EOF
APP_NAME=${APP_NAME}
APP_VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
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
VPN_PANEL_TYPE=${VPN_PANEL_TYPE}
REMNAWAVE_URL=${REMNAWAVE_URL}
REMNAWAVE_LOGIN=${REMNAWAVE_LOGIN}
REMNAWAVE_PASSWORD=${REMNAWAVE_PASS}
YOOKASSA_SHOP_ID=${YK_SHOP}
YOOKASSA_SECRET_KEY=${YK_SECRET}
CRYPTOBOT_TOKEN=${CRYPTOBOT_TOKEN}
HTTPS_PORT=${HTTPS_PORT:-443}
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
    success "nginx.conf создан (порт ${HTTPS_PORT})"

    # Останавливаем старые контейнеры
    docker compose -f docker-compose.prod.yml down 2>/dev/null || true

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
        [[ $i -eq 18 ]] && warn "App не стал healthy, продолжаю..."
        sleep 5
    done

    # Получаем SSL сертификат
    CERT_PATH="nginx/ssl/live/${DOMAIN}/fullchain.pem"
    if [[ -f "$CERT_PATH" ]]; then
        success "SSL сертификат уже существует, пропускаю"
    else
        info "Получаю SSL сертификат через certbot standalone..."

        # Устанавливаем certbot если нет
        if ! command -v certbot &>/dev/null; then
            info "Устанавливаю certbot..."
            apt-get update -qq
            apt-get install -y -qq certbot
        fi

        # Получаем сертификат (standalone — certbot сам поднимает HTTP сервер)
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

        # Копируем сертификаты в папку проекта
        info "Копирую сертификаты..."
        mkdir -p "nginx/ssl/live/${DOMAIN}"
        cp "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" "nginx/ssl/live/${DOMAIN}/"
        cp "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" "nginx/ssl/live/${DOMAIN}/"
        success "Сертификаты скопированы"

        # Настраиваем автообновление
        CRON_FILE="/etc/cron.d/vpn-certbot-renew"
        PROJECT_DIR="$(pwd)"
        cat > "$CRON_FILE" <<CRONEOF
0 3 * * * root certbot renew --quiet --standalone --pre-hook "docker compose -f ${PROJECT_DIR}/docker-compose.prod.yml stop nginx" --post-hook "cp /etc/letsencrypt/live/${DOMAIN}/fullchain.pem ${PROJECT_DIR}/nginx/ssl/live/${DOMAIN}/ && cp /etc/letsencrypt/live/${DOMAIN}/privkey.pem ${PROJECT_DIR}/nginx/ssl/live/${DOMAIN}/ && docker compose -f ${PROJECT_DIR}/docker-compose.prod.yml start nginx"
CRONEOF
        success "Автообновление сертификата настроено (каждый день в 3:00)"
    fi

    # Запускаем nginx с SSL
    info "Запускаю nginx с SSL..."
    docker compose -f docker-compose.prod.yml up -d nginx

    # Применяем миграции
    info "Применяю миграции БД..."
    sleep 5
    docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head

else
    # ── РАЗРАБОТКА ────────────────────────────────────────────────────────────
    info "Запускаю в режиме разработки..."
    docker compose down -v 2>/dev/null || true
    docker compose up -d db app nginx
    info "Жду запуска (12 сек)..."
    sleep 12
    info "Применяю миграции БД..."
    docker compose exec app uv run alembic upgrade head
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
echo ""
echo -e "  Логи:    ${YELLOW}docker compose -f docker-compose.prod.yml logs -f app${RESET}"
echo -e "  Стоп:    ${YELLOW}docker compose -f docker-compose.prod.yml down${RESET}"
echo -e "  Обновить: ${YELLOW}git pull && docker compose -f docker-compose.prod.yml build app && docker compose -f docker-compose.prod.yml up -d app${RESET}"
echo ""

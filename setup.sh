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
echo "╔══════════════════════════════════════════════════╗"
echo "║        VPN Dashboard — Setup & Deploy            ║"
echo "╚══════════════════════════════════════════════════╝"
echo -e "${RESET}"

for cmd in docker curl openssl; do
    command -v "$cmd" &>/dev/null || error "Не найден '$cmd'."
done
docker compose version &>/dev/null || error "Нужен Docker Compose v2."

echo ""
echo "Режим запуска:"
echo "  1) Продакшен (домен + SSL)"
echo "  2) Разработка (localhost)"
read -rp "Выбор [1/2]: " MODE
MODE=${MODE:-2}

# ── Ввод данных ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}── Основные ────────────────────────────────────────${RESET}"
read -rp "Название панели [VPN Dashboard]: " APP_NAME; APP_NAME=${APP_NAME:-"VPN Dashboard"}
read -rp "Telegram Bot Token: " BOT_TOKEN; [[ -z "$BOT_TOKEN" ]] && error "Обязателен"
read -rp "Telegram Admin IDs (например: 123456789): " ADMIN_IDS_RAW; [[ -z "$ADMIN_IDS_RAW" ]] && error "Обязателен"
ADMIN_IDS="[$(echo "$ADMIN_IDS_RAW" | tr -s ' ,' ',' | sed 's/^,//;s/,$//')]"
read -rp "Логин панели [admin]: " WEB_USER; WEB_USER=${WEB_USER:-admin}
read -rsp "Пароль панели (мин. 6 символов): " WEB_PASS; echo ""
[[ ${#WEB_PASS} -lt 6 ]] && error "Пароль слишком короткий"

echo ""
echo -e "${BOLD}── База данных ─────────────────────────────────────${RESET}"
read -rp "Имя БД [vpnbot]: " DB_NAME; DB_NAME=${DB_NAME:-vpnbot}
read -rp "Пользователь БД [postgres]: " DB_USER; DB_USER=${DB_USER:-postgres}
read -rsp "Пароль БД [postgres]: " DB_PASS; echo ""; DB_PASS=${DB_PASS:-postgres}

echo ""
echo -e "${BOLD}── PasarGuard / Marzban ────────────────────────────${RESET}"
read -rp "URL панели (например: https://panel.example.com:8012): " PASAR_URL
[[ -z "$PASAR_URL" ]] && error "Обязателен"
read -rp "Логин Marzban [admin]: " PASAR_LOGIN; PASAR_LOGIN=${PASAR_LOGIN:-admin}
read -rsp "Пароль Marzban: " PASAR_PASS; echo ""

echo ""
echo -e "${BOLD}── YooKassa (Enter = пропустить) ───────────────────${RESET}"
read -rp "Shop ID: " YK_SHOP; YK_SHOP=${YK_SHOP:-""}
read -rp "Secret Key: " YK_SECRET; YK_SECRET=${YK_SECRET:-""}

# ── Продакшен-специфичные ─────────────────────────────────────────────────────
if [[ "$MODE" == "1" ]]; then
    echo ""
    echo -e "${BOLD}── Домен и SSL ─────────────────────────────────────${RESET}"
    echo -e "${YELLOW}⚠️  Домен должен уже указывать A-записью на IP этого сервера!${RESET}"
    read -rp "Домен (без https://): " DOMAIN; [[ -z "$DOMAIN" ]] && error "Обязателен"
    read -rp "Email для Let's Encrypt: " LE_EMAIL; [[ -z "$LE_EMAIL" ]] && error "Обязателен"
    TG_PROTOCOL=webhook
    WEBHOOK_URL="https://${DOMAIN}/webhook/bot"
    ALLOWED_ORIGINS='["https://'"${DOMAIN}"'"]'
    PANEL_URL="https://${DOMAIN}/panel/"
else
    DOMAIN="localhost"
    TG_PROTOCOL=long
    WEBHOOK_URL="https://localhost/webhook/bot"
    ALLOWED_ORIGINS='["http://localhost:8000"]'
    PANEL_URL="http://localhost/panel/"
fi

# ── Генерация .env ────────────────────────────────────────────────────────────
info "Генерирую .env..."
cat > .env <<EOF
APP_NAME=${APP_NAME}
APP_VERSION=1.0.0
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
YOOKASSA_SHOP_ID=${YK_SHOP}
YOOKASSA_SECRET_KEY=${YK_SECRET}
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
[[ ! "$START" =~ ^[Yy]$ ]] && { info "Запустите вручную: docker compose up -d"; exit 0; }

if [[ "$MODE" == "1" ]]; then
    # ── ПРОДАКШЕН ─────────────────────────────────────────────────────────────

    # Настраиваем nginx.conf под домен
    info "Настраиваю nginx для ${DOMAIN}..."
    # Заменяем YOUR_DOMAIN если ещё не заменён
    if grep -q "YOUR_DOMAIN" nginx/nginx.conf 2>/dev/null; then
        sed -i "s/YOUR_DOMAIN/${DOMAIN}/g" nginx/nginx.conf
    fi

    # Создаём временный HTTP-only nginx конфиг для получения сертификата
    info "Создаю временный HTTP конфиг для certbot..."
    cat > /tmp/nginx_http_only.conf <<NGINXEOF
worker_processes auto;
events { worker_connections 1024; }
http {
    server {
        listen 80;
        server_name ${DOMAIN};
        location /.well-known/acme-challenge/ {
            root /var/www/certbot;
        }
        location / {
            proxy_pass http://app:8000;
            proxy_set_header Host \$host;
        }
    }
}
NGINXEOF

    # Останавливаем всё старое
    docker compose -f docker-compose.prod.yml down 2>/dev/null || true

    # Проверяем есть ли уже сертификат
    CERT_PATH="nginx/ssl/live/${DOMAIN}/fullchain.pem"
    if [[ -f "$CERT_PATH" ]]; then
        success "SSL сертификат уже существует, пропускаю получение"
        info "Запускаю все сервисы..."
        docker compose -f docker-compose.prod.yml up -d
    else
        info "Запускаю db + app для получения сертификата..."
        docker compose -f docker-compose.prod.yml up -d db app

        # Запускаем nginx с HTTP-only конфигом
        info "Запускаю nginx (HTTP only)..."
        docker run -d --name tmp_certbot_nginx \
            --network "$(basename $(pwd))_vpn_net" \
            -p 80:80 \
            -v /tmp/nginx_http_only.conf:/etc/nginx/nginx.conf:ro \
            -v certbot_www:/var/www/certbot \
            nginx:alpine 2>/dev/null || \
        docker run -d --name tmp_certbot_nginx \
            --network vpn_net \
            -p 80:80 \
            -v /tmp/nginx_http_only.conf:/etc/nginx/nginx.conf:ro \
            -v "$(basename $(pwd))_certbot_www:/var/www/certbot" \
            nginx:alpine 2>/dev/null || true

        sleep 5

        info "Получаю SSL сертификат для ${DOMAIN}..."
        mkdir -p nginx/ssl

        if docker compose -f docker-compose.prod.yml run --rm certbot certonly \
            --webroot \
            --webroot-path=/var/www/certbot \
            --email "${LE_EMAIL}" \
            --agree-tos \
            --no-eff-email \
            --non-interactive \
            -d "${DOMAIN}"; then
            success "SSL сертификат получен!"
        else
            warn "Не удалось получить SSL. Проверьте что домен указывает на этот сервер."
            warn "Запустите вручную после исправления:"
            warn "  docker compose -f docker-compose.prod.yml run --rm certbot certonly --webroot --webroot-path=/var/www/certbot --email ${LE_EMAIL} --agree-tos -d ${DOMAIN}"
        fi

        # Останавливаем временный nginx
        docker stop tmp_certbot_nginx 2>/dev/null || true
        docker rm tmp_certbot_nginx 2>/dev/null || true

        # Запускаем всё с SSL
        info "Запускаю все сервисы с SSL..."
        docker compose -f docker-compose.prod.yml up -d
    fi

    info "Жду запуска приложения (15 сек)..."
    sleep 15
    info "Применяю миграции БД..."
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
echo -e "  Логи:    ${YELLOW}docker compose logs -f app${RESET}"
echo -e "  Стоп:    ${YELLOW}docker compose down${RESET}"
echo ""

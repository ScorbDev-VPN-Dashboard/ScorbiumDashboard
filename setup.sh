#!/usr/bin/env bash
# =============================================================================
#  VPN Dashboard — интерактивный скрипт настройки и запуска
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

# ── Проверка зависимостей ─────────────────────────────────────────────────────
for cmd in docker curl openssl; do
    command -v "$cmd" &>/dev/null || error "Не найден '$cmd'. Установите его и повторите."
done
docker compose version &>/dev/null || error "Нужен Docker Compose v2. Обновите Docker."

# ── Режим ─────────────────────────────────────────────────────────────────────
echo ""
echo "Выберите режим запуска:"
echo "  1) Продакшен (домен + SSL + Nginx) — только на VPS/сервере"
echo "  2) Разработка (localhost, long polling) — для локального теста"
read -rp "Ваш выбор [1/2]: " MODE
MODE=${MODE:-2}

# =============================================================================
#  ВВОД ДАННЫХ
# =============================================================================

echo ""
echo -e "${BOLD}── Основные настройки ──────────────────────────────${RESET}"

read -rp "Название панели [VPN Dashboard]: " APP_NAME
APP_NAME=${APP_NAME:-"VPN Dashboard"}

read -rp "Telegram Bot Token: " BOT_TOKEN
[[ -z "$BOT_TOKEN" ]] && error "Bot Token обязателен"

read -rp "Telegram Admin IDs (через запятую, например: 123456789): " ADMIN_IDS_RAW
[[ -z "$ADMIN_IDS_RAW" ]] && error "Admin IDs обязательны"
ADMIN_IDS="[$(echo "$ADMIN_IDS_RAW" | tr -s ' ,' ',' | sed 's/^,//;s/,$//')]"

read -rp "Логин для панели [admin]: " WEB_USER
WEB_USER=${WEB_USER:-admin}

read -rsp "Пароль для панели (мин. 6 символов): " WEB_PASS
echo ""
[[ ${#WEB_PASS} -lt 6 ]] && error "Пароль слишком короткий"

echo ""
echo -e "${BOLD}── База данных ─────────────────────────────────────${RESET}"

read -rp "Имя БД [vpnbot]: " DB_NAME
DB_NAME=${DB_NAME:-vpnbot}
read -rp "Пользователь БД [postgres]: " DB_USER
DB_USER=${DB_USER:-postgres}
read -rsp "Пароль БД [postgres]: " DB_PASS
echo ""
[[ -z "$DB_PASS" ]] && DB_PASS="postgres"

echo ""
echo -e "${BOLD}── PasarGuard (Marzban) ────────────────────────────${RESET}"

read -rp "URL панели (например: https://panel.example.com:8012): " PASAR_URL
[[ -z "$PASAR_URL" ]] && error "URL панели обязателен"
read -rp "Логин Marzban [admin]: " PASAR_LOGIN
PASAR_LOGIN=${PASAR_LOGIN:-admin}
read -rsp "Пароль Marzban: " PASAR_PASS
echo ""
read -rp "API Key Marzban (если есть, иначе Enter): " PASAR_KEY
PASAR_KEY=${PASAR_KEY:-""}

echo ""
echo -e "${BOLD}── YooKassa (Enter чтобы пропустить) ───────────────${RESET}"
read -rp "Shop ID: " YK_SHOP; YK_SHOP=${YK_SHOP:-""}
read -rp "Secret Key: " YK_SECRET; YK_SECRET=${YK_SECRET:-""}

# ── Продакшен ─────────────────────────────────────────────────────────────────
if [[ "$MODE" == "1" ]]; then
    echo ""
    echo -e "${BOLD}── Продакшен: домен и SSL ──────────────────────────${RESET}"
    echo -e "${YELLOW}⚠️  Убедитесь что домен уже указывает A-записью на IP этого сервера!${RESET}"
    echo ""

    read -rp "Домен (без https://, например: vpn.example.com): " DOMAIN
    [[ -z "$DOMAIN" ]] && error "Домен обязателен"
    read -rp "Email для Let's Encrypt: " LE_EMAIL
    [[ -z "$LE_EMAIL" ]] && error "Email обязателен"

    SERVER_PORT=8000
    TG_PROTOCOL=webhook
    WEBHOOK_URL="https://${DOMAIN}/webhook/bot"
    ALLOWED_ORIGINS='["https://'"${DOMAIN}"'"]'
    SERVER_HOST="0.0.0.0"
    PANEL_URL="https://${DOMAIN}/panel/"
else
    DOMAIN="localhost"
    SERVER_PORT=8000
    TG_PROTOCOL=long
    WEBHOOK_URL="https://localhost/webhook/bot"
    ALLOWED_ORIGINS='["http://localhost:8000"]'
    SERVER_HOST="0.0.0.0"
    PANEL_URL="http://localhost:${SERVER_PORT}/panel/"
fi

# =============================================================================
#  ГЕНЕРАЦИЯ .env
# =============================================================================
info "Генерирую .env..."

cat > .env <<EOF
APP_NAME=${APP_NAME}
APP_VERSION=1.0.0

SERVER_HOST=${SERVER_HOST}
SERVER_PORT=${SERVER_PORT}
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
PASARGUARD_API_KEY=${PASAR_KEY}

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

# =============================================================================
#  NGINX (только продакшен)
# =============================================================================
if [[ "$MODE" == "1" ]]; then
    info "Настраиваю nginx для домена ${DOMAIN}..."
    sed -i.bak "s/YOUR_DOMAIN/${DOMAIN}/g" nginx/nginx.conf
    rm -f nginx/nginx.conf.bak
    success "nginx/nginx.conf обновлён"
fi

# =============================================================================
#  ЗАПУСК
# =============================================================================
echo ""
read -rp "Запустить Docker Compose? [Y/n]: " START
START=${START:-Y}

if [[ ! "$START" =~ ^[Yy]$ ]]; then
    echo ""
    info ".env создан. Запустите вручную: docker compose up -d"
    exit 0
fi

if [[ "$MODE" == "1" ]]; then
    # ── Продакшен ──────────────────────────────────────────────────────────────

    # Проверяем что домен резолвится на этот сервер
    info "Проверяю что домен ${DOMAIN} доступен..."
    SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "unknown")
    DOMAIN_IP=$(getent hosts "${DOMAIN}" 2>/dev/null | awk '{print $1}' || dig +short "${DOMAIN}" 2>/dev/null | head -1 || echo "unknown")

    if [[ "$SERVER_IP" != "unknown" && "$DOMAIN_IP" != "unknown" && "$SERVER_IP" != "$DOMAIN_IP" ]]; then
        warn "IP сервера: ${SERVER_IP}"
        warn "IP домена:  ${DOMAIN_IP}"
        warn "Домен указывает на другой сервер! SSL сертификат не будет получен."
        warn "Исправьте A-запись домена и запустите скрипт снова."
        echo ""
        read -rp "Продолжить без SSL? (запустит только HTTP) [y/N]: " NOSSLOK
        if [[ ! "$NOSSLOK" =~ ^[Yy]$ ]]; then
            exit 1
        fi
        # Запуск без nginx/certbot
        docker compose up -d db app
        info "Применяю миграции..."
        sleep 8
        docker compose exec app uv run alembic upgrade head
        PANEL_URL="http://${SERVER_IP}:${SERVER_PORT}/panel/"
    else
        # Запускаем db + app + nginx (HTTP пока)
        info "Запускаю сервисы..."
        docker compose down -v 2>/dev/null || true
        docker compose up -d db app nginx

        # Ждём пока app поднимется
        info "Жду запуска приложения..."
        sleep 10

        # Получаем SSL — с таймаутом 60 секунд
        info "Получаю SSL сертификат для ${DOMAIN} (таймаут 60 сек)..."
        if timeout 60 docker compose run --rm certbot certonly \
            --webroot \
            --webroot-path=/var/www/certbot \
            --email "${LE_EMAIL}" \
            --agree-tos \
            --no-eff-email \
            --non-interactive \
            -d "${DOMAIN}" 2>&1; then
            success "SSL сертификат получен!"
            docker compose restart nginx
        else
            warn "Не удалось получить SSL сертификат."
            warn "Возможные причины:"
            warn "  1. Домен не указывает на этот сервер"
            warn "  2. Порт 80 заблокирован файрволом"
            warn "  3. Nginx не запустился"
            warn ""
            warn "Панель доступна по HTTP: http://${DOMAIN}/panel/"
            warn "Для SSL запустите вручную:"
            warn "  docker compose run --rm certbot certonly --webroot --webroot-path=/var/www/certbot --email ${LE_EMAIL} --agree-tos -d ${DOMAIN}"
        fi

        # Миграции
        info "Применяю миграции БД..."
        sleep 5
        docker compose exec app uv run alembic upgrade head
    fi

else
    # ── Разработка ─────────────────────────────────────────────────────────────
    info "Запускаю в режиме разработки (без nginx)..."

    # Сносим старые контейнеры и volumes чтобы пароль БД совпал
    docker compose down -v 2>/dev/null || true
    docker compose up -d db app

    info "Жду запуска приложения..."
    sleep 8

    info "Применяю миграции БД..."
    docker compose exec app uv run alembic upgrade head
fi

# =============================================================================
#  ИТОГ
# =============================================================================
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║           ✅  Запуск завершён успешно!           ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  🌐 Панель:      ${BOLD}${CYAN}${PANEL_URL}${RESET}"
echo -e "  📖 API Docs:    ${CYAN}${PANEL_URL%panel/}docs${RESET}"
echo -e "  👤 Логин:       ${BOLD}${WEB_USER}${RESET}"
echo -e "  🔑 Пароль:      ${BOLD}${WEB_PASS}${RESET}"
echo -e "  🗄  БД пароль:  ${BOLD}${DB_PASS}${RESET}"
echo ""
echo -e "  Логи:    ${YELLOW}docker compose logs -f app${RESET}"
echo -e "  Стоп:    ${YELLOW}docker compose down${RESET}"
echo -e "  Рестарт: ${YELLOW}docker compose restart app${RESET}"
echo ""

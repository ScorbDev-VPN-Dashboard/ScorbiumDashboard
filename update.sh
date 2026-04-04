#!/usr/bin/env bash
# Обновление проекта на сервере
set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RESET='\033[0m'

# Берём домен из .env
DOMAIN=$(grep "^DOMAIN=" .env 2>/dev/null | cut -d= -f2 || echo "")
if [[ -z "$DOMAIN" ]]; then
    DOMAIN=$(grep TELEGRAM_WEBHOOK_URL .env | sed 's|.*https://||;s|/.*||' | sed 's|:.*||')
fi

echo -e "${CYAN}[1/4] git pull...${RESET}"
git pull

echo -e "${CYAN}[2/4] Пересобираю контейнеры...${RESET}"
docker compose -f docker-compose.prod.yml up -d --build

echo -e "${CYAN}[3/4] Применяю nginx конфиг...${RESET}"
# Генерируем конфиг с реальным доменом и копируем в контейнер
cat > /tmp/vpn_nginx.conf << NGINXEOF
limit_req_zone \$binary_remote_addr zone=panel:10m rate=30r/m;
limit_req_zone \$binary_remote_addr zone=api:10m rate=60r/m;
limit_req_zone \$binary_remote_addr zone=webhook:10m rate=120r/m;
upstream vpn_app { server app:8000; keepalive 32; }
server {
    listen 80;
    server_name ${DOMAIN};
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl http2;
    listen 8443 ssl http2;
    server_name ${DOMAIN};
    ssl_certificate     /etc/nginx/ssl/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/live/${DOMAIN}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    client_max_body_size 20M;
    location /panel/ {
        limit_req zone=panel burst=20 nodelay;
        proxy_pass http://vpn_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
    location /webhook/ {
        limit_req zone=webhook burst=50 nodelay;
        proxy_pass http://vpn_app;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location /api/ {
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
NGINXEOF

docker cp /tmp/vpn_nginx.conf vpn_nginx:/etc/nginx/conf.d/default.conf
docker exec vpn_nginx nginx -t && docker exec vpn_nginx nginx -s reload
echo -e "${GREEN}  nginx OK${RESET}"

echo -e "${CYAN}[4/4] Миграции БД...${RESET}"
sleep 3
docker compose -f docker-compose.prod.yml exec app uv run alembic upgrade head

echo -e "${GREEN}✅ Готово! Панель: https://${DOMAIN}/panel/${RESET}"

#!/usr/bin/env bash
set -euo pipefail
GREEN='\033[0;32m'; CYAN='\033[0;36m'; RESET='\033[0m'

DOMAIN=$(grep "^DOMAIN=" .env 2>/dev/null | cut -d= -f2 || echo "")
[[ -z "$DOMAIN" ]] && DOMAIN=$(grep TELEGRAM_WEBHOOK_URL .env | sed 's|.*https://||;s|/.*||;s|:.*||')

echo -e "${CYAN}[1/4] git pull...${RESET}"
git pull

# Обновляем APP_VERSION из pyproject.toml
NEW_VER=$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')
if [[ -n "$NEW_VER" ]]; then
    if grep -q "^APP_VERSION=" .env; then
        sed -i "s/^APP_VERSION=.*/APP_VERSION=${NEW_VER}/" .env
    else
        echo "APP_VERSION=${NEW_VER}" >> .env
    fi
    echo -e "${GREEN}  Версия: ${NEW_VER}${RESET}"
fi

echo -e "${CYAN}[2/4] Генерирую nginx.conf...${RESET}"
cat > nginx/nginx.conf << NGINXEOF
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 20M;
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;
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
        listen 443 ssl;
        listen 8443 ssl;
        http2 on;
        server_name ${DOMAIN};
        ssl_certificate /etc/nginx/ssl/live/${DOMAIN}/fullchain.pem;
        ssl_certificate_key /etc/nginx/ssl/live/${DOMAIN}/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:SSL:10m;
        location /panel/ { limit_req zone=panel burst=20 nodelay; proxy_pass http://vpn_app; proxy_set_header Host \$host; proxy_set_header X-Real-IP \$remote_addr; proxy_set_header X-Forwarded-Proto \$scheme; proxy_read_timeout 60s; }
        location /app/ { proxy_pass http://vpn_app; proxy_set_header Host \$host; proxy_set_header X-Forwarded-Proto \$scheme; }
        location /webhook/ { limit_req zone=webhook burst=50 nodelay; proxy_pass http://vpn_app; proxy_set_header Host \$host; proxy_set_header X-Forwarded-Proto \$scheme; }
        location /api/ { limit_req zone=api burst=30 nodelay; proxy_pass http://vpn_app; proxy_set_header Host \$host; proxy_set_header X-Forwarded-Proto \$scheme; }
        location = / { return 301 /panel/; }
        location / { proxy_pass http://vpn_app; proxy_set_header Host \$host; proxy_set_header X-Forwarded-Proto \$scheme; }
    }
}
NGINXEOF
echo -e "${GREEN}  nginx.conf готов для ${DOMAIN}${RESET}"

echo -e "${CYAN}[3/4] Пересобираю контейнеры...${RESET}"
docker compose -f docker-compose.prod.yml up -d --build

echo -e "${CYAN}[4/4] Перезагружаю nginx...${RESET}"
# Ждём пока nginx запустится
echo -n "  Ожидание nginx"
for i in $(seq 1 30); do
    STATUS=$(docker inspect --format='{{.State.Status}}' vpn_nginx 2>/dev/null || echo "none")
    if [[ "$STATUS" == "running" ]]; then
        break
    fi
    echo -n "."
    sleep 2
done
echo ""
docker exec vpn_nginx nginx -t && docker exec vpn_nginx nginx -s reload
echo -e "${GREEN}  nginx OK${RESET}"

echo -e "${GREEN}✅ Готово! https://${DOMAIN}/panel/${RESET}"

# Nginx + SSL Setup

## 1. Edit nginx.conf

Replace `YOUR_DOMAIN` with your actual domain in `nginx/nginx.conf`:
```
server_name your-domain.com;
ssl_certificate     /etc/nginx/ssl/live/your-domain.com/fullchain.pem;
ssl_certificate_key /etc/nginx/ssl/live/your-domain.com/privkey.pem;
```

## 2. First run — get SSL cert (HTTP only, no HTTPS yet)

Temporarily comment out the HTTPS server block in nginx.conf, then:

```bash
docker compose up -d nginx db app

# Get certificate
docker compose run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email your@email.com \
  --agree-tos \
  --no-eff-email \
  -d your-domain.com
```

## 3. Uncomment HTTPS block, restart nginx

```bash
docker compose restart nginx
```

## 4. Auto-renewal

The certbot service runs `certbot renew` every 12 hours automatically.

## 5. .env for production

```env
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
TELEGRAM_TYPE_PROTOCOL=webhook
TELEGRAM_WEBHOOK_URL=https://your-domain.com/webhook/bot
TELEGRAM_WEBHOOK_PATH=/webhook/bot
```

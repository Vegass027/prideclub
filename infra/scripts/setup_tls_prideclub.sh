#!/usr/bin/env bash
# Запустить ПОСЛЕ того как prideclub.fun и *.prideclub.fun указывают на этот сервер.
# Получает сертификаты Let's Encrypt, активирует HTTPS, включает auto-renewal.

set -euo pipefail

VPS_IP="45.153.188.254"
DOMAINS=(prideclub.fun api.prideclub.fun db.prideclub.fun)
EMAIL="admin@prideclub.fun"

RESOLVED=$(getent hosts prideclub.fun | awk '{print $1}' | head -1)
if [ "$RESOLVED" != "$VPS_IP" ]; then
  echo "ОШИБКА: prideclub.fun резолвится в $RESOLVED, ожидался $VPS_IP"
  echo "Обнови A-записи в панели управления доменом:"
  echo "  @         A    $VPS_IP"
  echo "  api       A    $VPS_IP"
  echo "  db        A    $VPS_IP"
  exit 1
fi

echo "=== Останавливаю nginx для standalone-проверки ==="
systemctl stop nginx || true

echo "=== Получаю сертификаты ==="
certbot certonly \
  --standalone \
  --non-interactive \
  --agree-tos \
  --email "$EMAIL" \
  --domains "${DOMAINS[@]}"

echo "=== Включаю auto-renewal ==="
( crontab -l 2>/dev/null | grep -v certbot-renew || true
  echo "0 3 * * * certbot renew --quiet --post-hook 'systemctl reload nginx'" ) | crontab -

echo "=== Переключаю nginx на TLS-конфиг ==="
cat > /etc/nginx/sites-available/prideclub.conf <<'NGINX'
upstream habit_backend { server 127.0.0.1:8000; }
upstream habit_frontend { server 127.0.0.1:5173; }
upstream habit_pgweb { server 127.0.0.1:8081; }

# === ACME challenge для renewal ===
server {
    listen 80;
    server_name prideclub.fun api.prideclub.fun db.prideclub.fun;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/html;
        default_type "text/plain";
    }
    location / {
        return 301 https://$host$request_uri;
    }
}

# === API backend ===
server {
    listen 443 ssl;
    server_name api.prideclub.fun;

    ssl_certificate     /etc/letsencrypt/live/prideclub.fun/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/prideclub.fun/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    client_max_body_size 50M;

    gzip on;
    gzip_types application/json application/javascript text/css text/plain;

    location / {
        proxy_pass http://habit_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# === Telegram Mini App (frontend) ===
server {
    listen 443 ssl;
    server_name prideclub.fun;

    ssl_certificate     /etc/letsencrypt/live/prideclub.fun/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/prideclub.fun/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    gzip on;
    gzip_types text/css application/javascript application/json image/svg+xml;

    location / {
        proxy_pass http://habit_frontend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /assets/ {
        proxy_pass http://habit_frontend;
        proxy_set_header Host $host;
        proxy_cache_valid 200 1y;
        add_header Cache-Control "public, immutable, max-age=31536000";
    }
}

# === pgweb (DB admin) ===
server {
    listen 443 ssl;
    server_name db.prideclub.fun;

    ssl_certificate     /etc/letsencrypt/live/prideclub.fun/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/prideclub.fun/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    auth_basic "PrideClub DB";
    auth_basic_user_file /etc/nginx/.htpasswd;

    allow 127.0.0.1;
    # allow 185.191.118.53;  # раскомментируй свой IP
    deny all;

    location / {
        proxy_pass http://habit_pgweb;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }
}
NGINX

nginx -t && systemctl start nginx

echo
echo "Готово!"
echo "  https://prideclub.fun       — Telegram Mini App"
echo "  https://api.prideclub.fun   — Backend API"
echo "  https://db.prideclub.fun    — pgweb (admin / <DB_PASSWORD>)"

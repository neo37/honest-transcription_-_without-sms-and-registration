#!/bin/bash
# Деплой meetrec на сервер: архив -> scp -> распаковка -> rebuild & up
# Использование: DEPLOY_HOST=... DEPLOY_PASS=... ./deploy.sh
# Для obed.pro (shared): DEPLOY_HOST=91.84.124.245 DEPLOY_PASS='...' ./deploy.sh

set -e
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

DEPLOY_HOST="${DEPLOY_HOST:-185.245.106.80}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_PASS="${DEPLOY_PASS:-}"
REMOTE_DIR="${REMOTE_DIR:-/opt/meetrec}"
ARCHIVE="meetrec-deploy.tar.gz"
ARCHIVE_PATH="/tmp/meetrec-deploy-$$.tar.gz"

# Shared server (obed.pro): bp, spacecode, prod-it
OBED_HOST="91.84.124.245"
SMART_SKI_NGINX="/root/smart_ski/nginx.conf"

if [ -z "$DEPLOY_PASS" ]; then
  echo "Укажите пароль: export DEPLOY_PASS='...' или DEPLOY_PASS='...' ./deploy.sh"
  exit 1
fi

echo "=== Упаковка архива ==="
GZIP=-1 tar --exclude='.git' \
    --exclude='.env' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='db.sqlite3' \
    --exclude='media' \
    --exclude='staticfiles' \
    --exclude='*.tar.gz' \
    -czf "$ARCHIVE_PATH" .
echo "Размер: $(du -h "$ARCHIVE_PATH" | cut -f1)"

echo "=== Копирование на сервер ==="
sshpass -p "$DEPLOY_PASS" scp -o StrictHostKeyChecking=no "$ARCHIVE_PATH" "${DEPLOY_USER}@${DEPLOY_HOST}:/tmp/$ARCHIVE"

echo "=== Распаковка и настройка на сервере ==="
sshpass -p "$DEPLOY_PASS" ssh -o StrictHostKeyChecking=no "${DEPLOY_USER}@${DEPLOY_HOST}" \
  "export REMOTE_DIR='$REMOTE_DIR' ARCHIVE='$ARCHIVE' DEPLOY_HOST='$DEPLOY_HOST' OBED_HOST='$OBED_HOST' SMART_SKI_NGINX='$SMART_SKI_NGINX'; $(cat << 'REMOTE_SCRIPT'
set -e
mkdir -p "$REMOTE_DIR"
cd "$REMOTE_DIR"
tar -xzf /tmp/meetrec-deploy.tar.gz -C .
rm -f /tmp/meetrec-deploy.tar.gz
touch .env

IS_OBED=false
[ "$DEPLOY_HOST" = "$OBED_HOST" ] && [ -d /root/smart_ski ] && IS_OBED=true

if [ "$IS_OBED" = true ]; then
  echo "Режим: shared server (obed.pro)"
  DOMAINS="bp.obed.pro,spacecode.obed.pro,prod-it.obed.pro"
  ORIGINS="https://bp.obed.pro,https://spacecode.obed.pro,https://prod-it.obed.pro"
  grep -q '^ALLOWED_HOSTS=' .env 2>/dev/null && sed -i "s|^ALLOWED_HOSTS=.*|ALLOWED_HOSTS=$DOMAINS,91.84.124.245,localhost|" .env || echo "ALLOWED_HOSTS=$DOMAINS,91.84.124.245,localhost" >> .env
  grep -q '^CSRF_TRUSTED_ORIGINS=' .env 2>/dev/null && sed -i "s|^CSRF_TRUSTED_ORIGINS=.*|CSRF_TRUSTED_ORIGINS=$ORIGINS|" .env || echo "CSRF_TRUSTED_ORIGINS=$ORIGINS" >> .env
  grep -q '^SESSION_COOKIE_SECURE=' .env 2>/dev/null && sed -i 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=True/' .env || echo "SESSION_COOKIE_SECURE=True" >> .env
  grep -q '^DJANGO_SUPERUSER_USERNAME=' .env 2>/dev/null || echo "DJANGO_SUPERUSER_USERNAME=admin" >> .env
  grep -q '^DJANGO_SUPERUSER_PASSWORD=' .env 2>/dev/null || echo "DJANGO_SUPERUSER_PASSWORD=MeetRecAdmin2025!" >> .env
  grep -q '^DJANGO_SUPERUSER_EMAIL=' .env 2>/dev/null || echo "DJANGO_SUPERUSER_EMAIL=admin@obed.pro" >> .env
  # Порт 18000 для прокси (не конфликтует с 8000)
  sed -i 's|127.0.0.1:8003:8000|18000:8000|' docker-compose.prod.yml 2>/dev/null || true
  if ! grep -q '18000:8000' docker-compose.prod.yml 2>/dev/null; then
    sed -i 's/8003:8000/18000:8000/' docker-compose.prod.yml 2>/dev/null || true
  fi
else
  echo "Режим: standalone"
  grep -q '^CSRF_TRUSTED_ORIGINS=' .env 2>/dev/null || echo 'CSRF_TRUSTED_ORIGINS=https://baza.business-pad.com' >> .env
  grep -q '^ALLOWED_HOSTS=' .env 2>/dev/null || echo 'ALLOWED_HOSTS=baza.business-pad.com,185.245.106.80,localhost' >> .env
fi

grep -q '^OCR_API_URL=' .env 2>/dev/null || echo 'OCR_API_URL=http://ocr:8001/ocr' >> .env
grep -q '^OCR_API_KEY=' .env 2>/dev/null || echo 'OCR_API_KEY=' >> .env

systemctl enable docker 2>/dev/null || true
docker ps -aq -f name=meetrec | xargs -r docker rm -f 2>/dev/null || true

echo "Сборка образов..."
if docker compose version &>/dev/null; then
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache web poller
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
else
  docker-compose -f docker-compose.yml -f docker-compose.prod.yml build --no-cache web poller
  docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
fi

echo "Ожидание старта (20 сек)..."
sleep 20

WEB_CONTAINER=$(docker ps -q -f name=meetrec.*web 2>/dev/null | head -1)
if [ -n "$WEB_CONTAINER" ]; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "Host: localhost" http://127.0.0.1:8003/login/ 2>/dev/null || curl -s -o /dev/null -w "%{http_code}" -H "Host: localhost" http://127.0.0.1:18000/login/ 2>/dev/null || echo "000")
  echo "Проверка web -> HTTP $HTTP_CODE"
  if [ "$HTTP_CODE" != "200" ] && [ "$HTTP_CODE" != "302" ]; then
    echo "--- Логи web ---"
    docker logs "$WEB_CONTAINER" 2>&1 | tail -40
  fi
fi

# Shared server: nginx proxy + SSL
if [ "$IS_OBED" = true ] && [ -f "$SMART_SKI_NGINX" ]; then
  echo "Настройка nginx proxy для meetrec..."
  cp "$SMART_SKI_NGINX" "${SMART_SKI_NGINX}.bak"
  # Удаляем старый meetrec-блок через awk
  awk '
    /server_name bp\.obed\.pro spacecode\.obed\.pro prod-it\.obed\.pro/ { in_bl=1; br=0 }
    in_bl { 
      for(i=1;i<=length($0);i++) { ch=substr($0,i,1); if(ch=="{")br++; if(ch=="}")br-- }
      if(br<0){in_bl=0}; next
    }
    {print}
  ' "$SMART_SKI_NGINX" > "${SMART_SKI_NGINX}.new" && mv "${SMART_SKI_NGINX}.new" "$SMART_SKI_NGINX"

  # Добавляем HTTP блок
  if ! grep -q 'server_name bp.obed.pro spacecode.obed.pro prod-it.obed.pro' "$SMART_SKI_NGINX" 2>/dev/null; then
    if [ -d /etc/letsencrypt/live/meetrec-obed ]; then
      cat >> "$SMART_SKI_NGINX" << 'NGX'
# Meetrec: bp/spacecode/prod-it.obed.pro (HTTP -> HTTPS)
server {
    listen 80;
    listen [::]:80;
    server_name bp.obed.pro spacecode.obed.pro prod-it.obed.pro;
    client_max_body_size 300m;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}
NGX
    else
      cat >> "$SMART_SKI_NGINX" << 'NGX'
# Meetrec: bp/spacecode/prod-it.obed.pro (HTTP, без SSL пока)
server {
    listen 80;
    listen [::]:80;
    server_name bp.obed.pro spacecode.obed.pro prod-it.obed.pro;
    client_max_body_size 300m;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / {
        proxy_pass http://host.docker.internal:18000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }
}
NGX
    fi
  fi

  # HTTPS блок
  if [ -d /etc/letsencrypt/live/meetrec-obed ]; then
    if ! grep -q 'ssl_certificate.*meetrec-obed' "$SMART_SKI_NGINX" 2>/dev/null; then
      cat >> "$SMART_SKI_NGINX" << 'NGXSSL'
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name bp.obed.pro spacecode.obed.pro prod-it.obed.pro;
    ssl_certificate /etc/letsencrypt/live/meetrec-obed/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/meetrec-obed/privkey.pem;
    client_max_body_size 300m;
    location / {
        proxy_pass http://host.docker.internal:18000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }
}
NGXSSL
    fi
  else
    if command -v certbot >/dev/null 2>&1; then
      mkdir -p /var/www/certbot
      certbot certonly --webroot -w /var/www/certbot -d bp.obed.pro -d spacecode.obed.pro -d prod-it.obed.pro --cert-name meetrec-obed -n --agree-tos -m admin@obed.pro 2>/dev/null || true
    fi
    if [ -d /etc/letsencrypt/live/meetrec-obed ]; then
      grep -q 'ssl_certificate.*meetrec-obed' "$SMART_SKI_NGINX" 2>/dev/null || cat >> "$SMART_SKI_NGINX" << 'NGXSSL2'
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name bp.obed.pro spacecode.obed.pro prod-it.obed.pro;
    ssl_certificate /etc/letsencrypt/live/meetrec-obed/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/meetrec-obed/privkey.pem;
    client_max_body_size 300m;
    location / {
        proxy_pass http://host.docker.internal:18000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }
}
NGXSSL2
    else
      echo "Certbot не получил серты. Сайт работает по HTTP (прокси на meetrec)"
    fi
  fi

  PROXY=$(docker ps -q -f name=smart_ski.*frontend 2>/dev/null | head -1)
  [ -n "$PROXY" ] && docker exec "$PROXY" nginx -t 2>/dev/null && docker restart "$PROXY" 2>/dev/null || true
  sleep 2
fi

echo "--- Контейнеры meetrec ---"
docker ps -a --filter name=meetrec --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
REMOTE_SCRIPT
)"

rm -f "$ARCHIVE_PATH"
if [ "$DEPLOY_HOST" = "$OBED_HOST" ]; then
  echo "=== Деплой завершён. Сайты: https://bp.obed.pro https://spacecode.obed.pro https://prod-it.obed.pro ==="
  echo "Суперадмин: admin / MeetRecAdmin2025! (Django /admin/)"
  echo "Если HTTPS не работает — получите серты: certbot certonly --webroot -w /var/www/certbot -d bp.obed.pro -d spacecode.obed.pro -d prod-it.obed.pro --cert-name meetrec-obed -n"
else
  echo "=== Деплой завершён ==="
fi

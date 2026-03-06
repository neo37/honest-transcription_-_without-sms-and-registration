#!/bin/bash
# Подключение по sshpass к серверу и установка всего необходимого:
# Docker, docker-compose, зависимости в контейнере (ffmpeg уже в Dockerfile).
# Использование: DEPLOY_PASS='...' ./install-server.sh
# Опционально: DEPLOY_HOST=... DEPLOY_USER=... REMOTE_DIR=...

set -e
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

DEPLOY_HOST="${DEPLOY_HOST:-185.245.106.80}"
DEPLOY_USER="${DEPLOY_USER:-root}"
DEPLOY_PASS="${DEPLOY_PASS:-}"
REMOTE_DIR="${REMOTE_DIR:-/opt/meetrec}"

if [ -z "$DEPLOY_PASS" ]; then
  echo "Укажите пароль: export DEPLOY_PASS='...' или DEPLOY_PASS='...' ./install-server.sh"
  exit 1
fi

echo "=== Подключение к ${DEPLOY_USER}@${DEPLOY_HOST} и установка ==="
sshpass -p "$DEPLOY_PASS" ssh -o StrictHostKeyChecking=no "${DEPLOY_USER}@${DEPLOY_HOST}" "REMOTE_DIR='$REMOTE_DIR'; $(cat << 'REMOTE_SCRIPT'
set -e
echo "--- Проверка Docker на хосте ---"
if ! command -v docker &>/dev/null; then
  echo "Установка Docker..."
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
if ! docker compose version &>/dev/null && ! docker-compose version &>/dev/null; then
  echo "Установка docker-compose (standalone)..."
  apt-get update -qq
  apt-get install -y -qq docker-compose-plugin 2>/dev/null || {
    curl -sL "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
  }
fi
systemctl enable docker 2>/dev/null || true
systemctl start docker 2>/dev/null || true

echo "--- Переход в каталог проекта и пересборка контейнеров ---"
mkdir -p "$REMOTE_DIR"
cd "$REMOTE_DIR"
if [ -f docker-compose.yml ]; then
  docker compose version &>/dev/null && COMPOSE="docker compose" || COMPOSE="docker-compose"
  $COMPOSE -f docker-compose.yml -f docker-compose.prod.yml up -d --build 2>/dev/null || \
  $COMPOSE -f docker-compose.yml -f docker-compose.prod.yml up -d --build
  echo "Контейнеры пересобраны и запущены (в образе уже есть ffmpeg)."
else
  echo "В $REMOTE_DIR нет docker-compose.yml. Сначала выполните деплой: ./deploy.sh"
fi
REMOTE_SCRIPT
)"

echo "=== Готово. Сервер настроен, контейнеры запущены. ==="

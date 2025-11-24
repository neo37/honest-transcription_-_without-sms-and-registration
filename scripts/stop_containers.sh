#!/bin/bash

# Скрипт для остановки текущих контейнеров

echo "🛑 Останавливаю контейнеры..."

# Останавливаем и удаляем контейнеры
docker stop cpq-frontend-1 backend postgres redis celery_worker celery_beat 2>/dev/null || true
docker rm cpq-frontend-1 backend postgres redis celery_worker celery_beat 2>/dev/null || true

echo "✅ Контейнеры остановлены"


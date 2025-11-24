#!/bin/bash

# Скрипт для запуска визуальных тестов на локальной машине
# Требует установленный Chrome и ChromeDriver

set -e

echo "👁️  Запуск визуальных тестов с Selenium..."
echo "⚠️  Убедитесь, что:"
echo "   1. Chrome/Chromium установлен"
echo "   2. ChromeDriver установлен и доступен в PATH"
echo "   3. X server запущен (DISPLAY установлен)"
echo ""

# Проверяем наличие ChromeDriver
if ! command -v chromedriver &> /dev/null; then
    echo "❌ ChromeDriver не найден!"
    echo "Установите ChromeDriver:"
    echo "  sudo apt-get install chromium-chromedriver"
    echo "  или скачайте с https://chromedriver.chromium.org/"
    exit 1
fi

# Проверяем DISPLAY
if [ -z "$DISPLAY" ]; then
    echo "⚠️  DISPLAY не установлен, используем :0"
    export DISPLAY=:0
fi

echo "✅ Запускаю визуальные тесты..."
echo "🌐 Браузер откроется автоматически - наблюдайте процесс!"
echo ""

# Активируем виртуальное окружение если есть
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Запускаем тесты
pytest transcribe/tests/test_visual_scenarios.py \
    --verbose \
    -v \
    -m visual \
    -s \
    --tb=short

echo ""
echo "✅ Визуальные тесты завершены!"



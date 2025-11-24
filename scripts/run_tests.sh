#!/bin/bash

# Скрипт для запуска тестов и получения отчёта

set -e

echo "🚀 Запуск тестов..."

# Создаём директории для отчётов
mkdir -p test-results test-reports

# Запускаем тесты в контейнере
docker-compose run --rm test

# Проверяем результаты
if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Тесты успешно завершены!"
    echo ""
    echo "📊 Отчёты доступны в:"
    echo "   - HTML отчёт: test-reports/report.html"
    echo "   - Покрытие кода: test-reports/coverage/index.html"
    echo "   - JUnit XML: test-results/junit.xml"
    echo ""
    
    # Открываем отчёт в браузере (если доступен)
    if command -v xdg-open &> /dev/null; then
        echo "🌐 Открываю HTML отчёт..."
        xdg-open test-reports/report.html 2>/dev/null || true
    elif command -v open &> /dev/null; then
        echo "🌐 Открываю HTML отчёт..."
        open test-reports/report.html 2>/dev/null || true
    fi
else
    echo ""
    echo "❌ Тесты завершились с ошибками"
    echo "📊 Проверьте отчёты в test-reports/"
    exit 1
fi



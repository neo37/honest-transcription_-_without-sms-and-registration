#!/bin/bash
# Скрипт для запуска системы

echo "Запуск системы автоматизации поиска и распределения транспорта..."

# Проверка наличия виртуального окружения
if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv venv
fi

# Активация виртуального окружения
source venv/bin/activate

# Установка зависимостей
echo "Установка зависимостей..."
pip install -r requirements.txt

# Проверка наличия .env файла
if [ ! -f ".env" ]; then
    echo "Создание .env файла из примера..."
    cp .env.example .env
    echo "Пожалуйста, отредактируйте .env файл и укажите необходимые параметры"
fi

echo ""
echo "Для запуска Telegram бота выполните:"
echo "  python bot.py"
echo ""
echo "Для запуска REST API выполните (в отдельном терминале):"
echo "  python api.py"
echo "  или"
echo "  uvicorn api:app --host 0.0.0.0 --port 8000"
echo ""


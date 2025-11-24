#!/bin/bash

# Скрипт для деплоя Image Generator на сервер

SERVER="root@91.84.124.245"
PASS="2-hf3r94QCxpC7VscB=g"
REMOTE_DIR="/root/image_generator"

echo "Создаю архив проекта..."
tar -czf image_generator.tar.gz app.py requirements.txt README.md nginx.conf image-generator.service deploy.sh

echo "Копирую файлы на сервер..."
sshpass -p "$PASS" scp -o StrictHostKeyChecking=no image_generator.tar.gz "$SERVER:/tmp/"

echo "Распаковываю на сервере..."
sshpass -p "$PASS" ssh -o StrictHostKeyChecking=no "$SERVER" << 'ENDSSH'
    mkdir -p /root/image_generator
    cd /root/image_generator
    tar -xzf /tmp/image_generator.tar.gz
    rm /tmp/image_generator.tar.gz
    
    # Создаем виртуальное окружение если его нет
    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi
    
    # Устанавливаем зависимости
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    
    # Создаем директорию для изображений
    mkdir -p /var/www/generated_images
    chmod 755 /var/www/generated_images
    
    # Копируем nginx конфигурацию
    cp nginx.conf /etc/nginx/sites-available/image-generator
    ln -sf /etc/nginx/sites-available/image-generator /etc/nginx/sites-enabled/
    
    # Проверяем конфигурацию nginx
    nginx -t
    
    # Перезагружаем nginx
    systemctl reload nginx
    
    # Получаем SSL сертификат если его еще нет
    if [ ! -f /etc/letsencrypt/live/voice.rity.lol/fullchain.pem ]; then
        echo "Получаю SSL сертификат..."
        certbot --nginx -d voice.rity.lol --non-interactive --agree-tos --email admin@rity.lol || echo "Не удалось получить сертификат автоматически"
    fi
    
    # Копируем и активируем systemd service
    cp image-generator.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable image-generator
    systemctl restart image-generator
    
    echo "Деплой завершен!"
    systemctl status image-generator
    echo ""
    echo "Проверьте логи: journalctl -u image-generator -f"
ENDSSH

echo "Очищаю локальный архив..."
rm image_generator.tar.gz

echo "Готово! Проверьте статус: sshpass -p '$PASS' ssh $SERVER 'systemctl status image-generator'"


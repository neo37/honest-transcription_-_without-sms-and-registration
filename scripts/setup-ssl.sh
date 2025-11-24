#!/bin/bash
# Скрипт для установки SSL сертификатов

echo 'Проверка DNS записей...'
AUDIO_IP=$(dig +short audio.repa.rest A)
VOICE_IP=$(dig +short voice.repa.rest A)

if [ -z "$AUDIO_IP" ] || [ -z "$VOICE_IP" ]; then
    echo 'DNS записи еще не распространились. Подождите несколько минут и попробуйте снова.'
    exit 1
fi

echo "DNS записи найдены: audio.repa.rest -> $AUDIO_IP, voice.repa.rest -> $VOICE_IP"
echo 'Получение SSL сертификатов...'

certbot --nginx -d audio.repa.rest -d voice.repa.rest --non-interactive --agree-tos --email admin@repa.rest --redirect

if [ $? -eq 0 ]; then
    echo 'SSL сертификаты успешно установлены!'
    systemctl reload nginx
else
    echo 'Ошибка при получении сертификатов'
    exit 1
fi

# Инструкция по настройке на сервере

## Информация о сервере
- IP: 91.84.124.245
- URL: voice.repa.rest
- Username: root
- OS: Ubuntu (с GPU)

## Шаги настройки

### 1. Подключение к серверу
```bash
ssh root@91.84.124.245
# Введите пароль: 6ee4ZdBuxY6Z339~6KpC
```

### 2. Установка необходимых компонентов

```bash
# Обновление системы
apt update && apt upgrade -y

# Установка Docker и Docker Compose
apt install -y docker.io docker-compose
systemctl enable docker
systemctl start docker

# Установка Git (если нужно клонировать репозиторий)
apt install -y git

# Установка других зависимостей
apt install -y python3 python3-pip ffmpeg nginx certbot python3-certbot-nginx
```

### 3. Клонирование/копирование проекта

Если проект в Git:
```bash
cd /root
git clone <repository_url> whisper-transcribe
cd whisper-transcribe
```

Или скопируйте файлы проекта в `/root/whisper-transcribe/`

### 4. Настройка проекта

```bash
cd /root/whisper-transcribe

# Создание необходимых директорий
mkdir -p media/screenshots staticfiles

# Настройка прав
chmod +x manage.py
```

### 5. Обновление настроек для production

Отредактируйте `whisper_transcribe/settings.py`:

```python
DEBUG = False
ALLOWED_HOSTS = ['voice.repa.rest', '91.84.124.245', 'localhost']
SECRET_KEY = 'ваш-секретный-ключ-для-production'  # Сгенерируйте новый!

# Настройки для production
STATIC_ROOT = '/var/www/staticfiles'
MEDIA_ROOT = '/var/www/media'
```

### 6. Настройка Docker Compose

Убедитесь, что `docker-compose.yml` настроен правильно для production.

### 7. Запуск через Docker

```bash
# Сборка образов
docker-compose build

# Применение миграций
docker-compose run --rm web python manage.py migrate --noinput

# Сбор статики
docker-compose run --rm web python manage.py collectstatic --noinput

# Запуск контейнеров
docker-compose up -d

# Проверка статуса
docker-compose ps
docker-compose logs -f
```

### 8. Настройка Nginx

Создайте файл `/etc/nginx/sites-available/voice.repa.rest`:

```nginx
server {
    listen 80;
    server_name voice.repa.rest;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias /var/www/staticfiles/;
    }

    location /media/ {
        alias /var/www/media/;
    }
}
```

Активируйте конфигурацию:
```bash
ln -s /etc/nginx/sites-available/voice.repa.rest /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 9. Настройка SSL (Let's Encrypt)

```bash
certbot --nginx -d voice.repa.rest
```

### 10. Проверка работы

```bash
# Проверка контейнеров
docker-compose ps

# Проверка логов
docker-compose logs web

# Проверка доступности
curl http://localhost:8000/
curl https://voice.repa.rest/
```

## Важные изменения в проекте

1. **Добавлена модель IPUploadCount** - отслеживание загрузок по IP
2. **Проверка оплаты** - после 3-й загрузки требуется оплата 12 рублей
3. **Футер с информацией для эквайринга**:
   - Реквизиты ИП
   - Способы оплаты
   - Условия возврата
   - Политика конфиденциальности
   - Стоимость услуг

## Миграции

После копирования проекта на сервер выполните:
```bash
docker-compose run --rm web python manage.py migrate
```

## Создание суперпользователя (опционально)

```bash
docker-compose run --rm web python manage.py createsuperuser
```

## Мониторинг

```bash
# Логи приложения
docker-compose logs -f web

# Статус контейнеров
docker-compose ps

# Использование ресурсов
docker stats
```


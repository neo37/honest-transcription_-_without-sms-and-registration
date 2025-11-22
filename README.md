**Русский:** `честная-транскрибация`
**English:** `honest-transcription`

## Описание

**Русский:**
```
Веб-приложение для честной транскрибации аудио и видео файлов. Быстрая обработка через faster-whisper, поддержка множественной загрузки, извлечение скриншотов из видео, система фраз-паролей и публичных ссылок.
```

**English:**
```
Web application for honest transcription of audio and video files. Fast processing via faster-whisper, multiple file upload support, video screenshot extraction, password phrase system and public sharing links.
```

## README с оглавлением

```markdown
# 🎤 Честная транскрибация / Honest Transcription

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.2.8-green.svg)](https://www.djangoproject.com/)
[![faster-whisper](https://img.shields.io/badge/faster--whisper-latest-orange.svg)](https://github.com/guillaumekln/faster-whisper)

Веб-приложение для транскрибации аудио и видео файлов с использованием faster-whisper. Поддержка множественной загрузки, извлечения скриншотов из видео, системы фраз-паролей и публичных ссылок для обмена.

Web application for transcribing audio and video files using faster-whisper. Supports multiple file uploads, video screenshot extraction, password phrase system and public sharing links.

## 📑 Оглавление / Table of Contents

- [Возможности / Features](#возможности--features)
- [Демо / Demo](#демо--demo)
- [Установка / Installation](#установка--installation)
- [Использование / Usage](#использование--usage)
- [Технологии / Technologies](#технологии--technologies)
- [Структура проекта / Project Structure](#структура-проекта--project-structure)
- [Настройка / Configuration](#настройка--configuration)
- [Развертывание / Deployment](#развертывание--deployment)
- [API / API](#api--api)
- [Безопасность / Security](#безопасность--security)
- [Лицензия / License](#лицензия--license)

## ✨ Возможности / Features

### Основные функции / Core Features

- 🎵 **Транскрибация аудио и видео** / **Audio and video transcription**
  - Поддержка файлов до 500 МБ / Support for files up to 500 MB
  - Автоматическое извлечение аудио дорожки / Automatic audio track extraction
  - Множественная загрузка файлов / Multiple file upload support

- 🖼️ **Извлечение скриншотов** / **Screenshot extraction**
  - Автоматическое извлечение скриншотов из видео / Automatic screenshot extraction from video
  - Скриншоты каждую минуту / Screenshots every minute
  - Просмотр в формате комикса/презентации / Comic/presentation style viewing

- 🔐 **Система доступа** / **Access system**
  - Фразы-пароли для приватности / Password phrases for privacy
  - Публичные ссылки для обмена / Public sharing links
  - Группировка файлов по сессиям / File grouping by sessions

- 📥 **Экспорт данных** / **Data export**
  - Скачивание текста транскрипций / Download transcription text
  - Скачивание скриншотов архивом / Download screenshots as archive
  - Скачивание общего текста сессии / Download session text

- 🎨 **Интерфейс** / **Interface**
  - Необруталистический дизайн / Neobrutalism design
  - Адаптивная верстка / Responsive layout
  - Анимации и визуализация прогресса / Animations and progress visualization

## 🚀 Демо / Demo

**Live Demo:** https://audio.repa.rest / https://voice.repa.rest

## 📦 Установка / Installation

### Требования / Requirements

- Python 3.10+
- Django 5.2.8
- faster-whisper
- FFmpeg
- Gunicorn
- Nginx (для production)

### Установка зависимостей / Install Dependencies

```bash
# Создать виртуальное окружение / Create virtual environment
python3 -m venv whisper_env
source whisper_env/bin/activate

# Установить зависимости / Install dependencies
pip install django faster-whisper gunicorn

# Установить FFmpeg / Install FFmpeg
sudo apt-get install ffmpeg
```

### Настройка проекта / Project Setup

```bash
# Клонировать репозиторий / Clone repository
git clone <repository-url>
cd honest-transcription

# Применить миграции / Run migrations
python manage.py migrate

# Создать суперпользователя / Create superuser
python manage.py createsuperuser

# Собрать статические файлы / Collect static files
python manage.py collectstatic
```

## 💻 Использование / Usage

### Загрузка файлов / File Upload

1. Откройте главную страницу / Open main page
2. Выберите файлы (до 500 МБ каждый) / Select files (up to 500 MB each)
3. Опционально: укажите подпись и фразу-пароль / Optional: add signature and password phrase
4. Включите извлечение скриншотов для видео / Enable screenshot extraction for video
5. Нажмите "Загрузить и транскрибировать" / Click "Upload and transcribe"

### Просмотр транскрипций / View Transcriptions

- Все транскрипции видны на главной странице / All transcriptions visible on main page
- Для приватных транскрипций войдите по фразе-паролю / For private transcriptions, login with password phrase
- Просмотр в формате комикса доступен по клику / Comic style viewing available on click

### Публичные ссылки / Public Links

- Каждая транскрипция имеет публичную ссылку / Each transcription has a public link
- Ссылки с паролем доступны для защищенных транскрипций / Password-protected links available for secured transcriptions
- Копирование ссылки одним кликом / One-click link copying

## 🛠️ Технологии / Technologies

- **Backend:**
  - Django 5.2.8
  - faster-whisper (OpenAI Whisper)
  - SQLite
  - Gunicorn

- **Frontend:**
  - HTML5 / CSS3
  - JavaScript (Vanilla)
  - Neobrutalism design

- **Infrastructure:**
  - Nginx (reverse proxy)
  - Systemd (service management)
  - Let's Encrypt (SSL)

## 📁 Структура проекта / Project Structure

```
honest-transcription/
├── whisper_transcribe/      # Django project settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── transcribe/              # Main application
│   ├── models.py            # Transcription, Screenshot models
│   ├── views.py             # Business logic
│   ├── urls.py              # URL routing
│   ├── admin.py             # Django admin configuration
│   └── templates/           # HTML templates
│       └── transcribe/
│           ├── index.html   # Main page
│           └── detail.html # Transcription detail (comic style)
├── media/                   # Uploaded files and screenshots
├── staticfiles/             # Static files
└── manage.py
```

## ⚙️ Настройка / Configuration

### Настройки Django / Django Settings

Основные настройки в `whisper_transcribe/settings.py`:

- `FILE_UPLOAD_MAX_MEMORY_SIZE = 524288000` (500 MB)
- `MEDIA_ROOT = '/var/www/media'`
- `STATIC_ROOT = '/var/www/staticfiles'`

### Модель Whisper / Whisper Model

По умолчанию используется модель `base` с `compute_type="int8"` для оптимальной производительности.

## 🚢 Развертывание / Deployment

### Systemd Service

```bash
# Создать сервис / Create service
sudo nano /etc/systemd/system/whisper-transcribe.service
```

```ini
[Unit]
Description=Whisper Transcribe Django Application
After=network.target

[Service]
User=root
WorkingDirectory=/root
Environment="PATH=/root/whisper_env/bin"
ExecStart=/root/whisper_env/bin/gunicorn --bind 0.0.0.0:8000 --workers 2 --timeout 300 whisper_transcribe.wsgi:application

[Install]
WantedBy=multi-user.target
```

### Nginx Configuration

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    
    client_max_body_size 500M;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /static/ {
        alias /var/www/staticfiles/;
    }
    
    location /media/ {
        alias /var/www/media/;
    }
}
```

## 🔌 API / API

### Endpoints

- `GET /` - Главная страница / Main page
- `POST /upload/` - Загрузка файлов / File upload
- `GET /transcription/<id>/` - Детали транскрипции / Transcription details
- `GET /public/<token>/` - Публичный доступ / Public access
- `GET /transcription/<id>/download-text/` - Скачать текст / Download text
- `POST /login/` - Вход по фразе-паролю / Login with password phrase
- `POST /clear-disk/` - Очистка диска (требует пароль админа) / Clear disk (requires admin password)

## 🔒 Безопасность / Security

- Фразы-пароли хешируются через SHA256
- Публичные токены генерируются случайным образом
- Проверка доступа на всех защищенных endpoints
- Ограничение размера файлов
- Валидация типов файлов

## 📄 Лицензия / License

MIT License

## 👤 Автор / Author

Создано для честной транскрибации переговоров / Created for honest transcription of conversations

---

**Made with ❤️ using Django and faster-whisper**
```

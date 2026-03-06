# BusinessPad — Meeting Transcription & Knowledge Platform

> Платформа для автоматической транскрибации встреч, OCR документов и накопления знаний организации.

---

## English

### What is BusinessPad?

BusinessPad is a self-hosted platform for teams and organizations that turns meeting recordings into structured knowledge. Upload a meeting recording — get a full transcription, an AI-generated summary, and instant answers to any question about the meeting content. Everything is stored in a built-in wiki knowledge base, linked directly to the original recordings.

### Key Features

**Transcription**
- Upload audio/video files (MP3, MP4, WAV, M4A, and more) for automatic transcription via Whisper
- Choose transcription quality (standard / high) and language (Russian, English, auto-detect)
- AI-generated title and summary for each recording
- Full-text search and semantic (vector) search across all transcriptions

**OCR**
- Extract text from PDF, PNG, and JPEG documents
- REST API for programmatic document processing
- Results returned as clean Markdown

**Knowledge Base (Wiki)**
- Hierarchical wiki linked to recordings
- Ask a question about any meeting — the AI answers and automatically creates a wiki article:
  - Parent page: meeting summary
  - Child page: your question + detailed answer
- The question is also posted as a comment on the recording with a link to the wiki article
- Full-text search, article versioning, public share links, article merging
- Markdown editor with toolbar

**Organizations & Spaces**
- Multi-tenant: each organization gets its own isolated space
- Member management within a space
- Organization registration via Telegram bot (no email confirmation needed)
- Magic login links — one click, no password required

**REST API**
- OCR submission and retrieval by URL
- Organization management (master key)
- Per-space UUID API keys

**MCP Server**
- `businesspad-mcp` — integrate BusinessPad tools directly into Claude Desktop, Claude Code, or any MCP-compatible AI assistant
- Tools: `ocr_submit_url`, `ocr_get_status`, `ocr_extract`, `ocr_list_done`, `org_create`

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 4.x, Gunicorn |
| Database | PostgreSQL + pgvector (semantic search) |
| Transcription | faster-whisper (separate OCR/transcription microservice) |
| Storage | S3-compatible object storage |
| Containerization | Docker + Docker Compose |
| Bot | Telegram Bot API |
| Frontend | Vanilla JS, custom CSS (no framework) |

### Quick Start

```bash
# 1. Clone
git clone https://github.com/neo37/honest-transcription_-_without-sms-and-registration.git
cd honest-transcription_-_without-sms-and-registration

# 2. Configure environment
cp .env.example .env
# Edit .env — set SECRET_KEY, database credentials, S3 settings, Telegram token, SITE_URL

# 3. Build and run
docker compose up -d --build

# 4. Open
open http://localhost:8000
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key |
| `DATABASE_URL` | PostgreSQL connection string |
| `S3_ENDPOINT_URL` | S3-compatible storage endpoint |
| `S3_ACCESS_KEY` | S3 access key |
| `S3_SECRET_KEY` | S3 secret key |
| `S3_BUCKET` | S3 bucket name |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_BOT_USERNAME` | Bot username (without @) |
| `SITE_URL` | Public URL of the platform (required for magic links and webhooks) |
| `MASTER_API_KEY` | Admin API key for org management via API |
| `DADATA_TOKEN` | DaData API token (Russian company lookup by name/INN) |

### API Reference

#### OCR (space API key)

```bash
# Submit document by URL
curl -X POST https://your-domain/api/v1/ocr/ \
  -H "X-Api-Key: YOUR_SPACE_UUID" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/doc.pdf", "filename": "doc.pdf"}'
# → {"task_id": 42, "status": "pending"}

# Poll for result
curl https://your-domain/api/v1/ocr/42/ \
  -H "X-Api-Key: YOUR_SPACE_UUID"
# → {"task_id": 42, "status": "done", "markdown": "# Document\n..."}

# List completed jobs
curl https://your-domain/api/space/YOUR_SPACE_UUID/ocr/
# → {"space": "Acme", "results": [...]}
```

#### Organization Management (master key)

```bash
curl -X POST https://your-domain/api/v1/org/ \
  -H "X-Api-Key: MASTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Corp", "email": "admin@acme.com"}'
# → {"org_id": 1, "api_key": "uuid", "password": "...", "magic_link": "https://..."}
```

#### MCP Server (Claude integration)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "businesspad": {
      "command": "businesspad-mcp",
      "env": {
        "BUSINESSPAD_API_KEY": "your-space-uuid",
        "BUSINESSPAD_BASE_URL": "https://your-domain"
      }
    }
  }
}
```

### Project Structure

```
.
├── recordings/              # Core app: recordings, transcription, OCR, auth, API
│   ├── models.py            # Recording, Space, SiteUser, OcrJob, Comment, ...
│   ├── views.py             # All views and REST API endpoints
│   ├── services.py          # S3 poller, transcription logic, LLM calls
│   ├── telegram_service.py  # Telegram bot handler
│   ├── s3_client.py         # S3 upload/download helpers
│   └── templates/           # HTML templates
├── wiki_kb/                 # Wiki knowledge base app
│   ├── models.py            # WikiArticle, WikiRevision
│   └── views.py             # CRUD, search, merge, share, history
├── businesspad-mcp/         # MCP server for AI assistant integration
├── ocr_server/              # Standalone OCR microservice (faster-whisper)
├── docker-compose.yml       # Local development stack
└── Dockerfile               # Main web container
```

---

## Русский

### Что такое BusinessPad?

BusinessPad — self-hosted платформа для команд и организаций, которая превращает записи встреч в структурированные знания. Загрузите запись встречи — получите полную транскрипцию, AI-резюме и возможность задавать любые вопросы по её содержанию. Всё хранится во встроенной вики, привязанной к оригинальным записям.

### Ключевые возможности

**Транскрибация**
- Загрузка аудио/видео файлов (MP3, MP4, WAV, M4A и др.) для автоматической транскрибации через Whisper
- Выбор качества транскрибации (стандарт / высокое) и языка (русский, английский, авто)
- AI-заголовок и резюме для каждой записи
- Полнотекстовый поиск и семантический (векторный) поиск по всем транскрипциям

**OCR**
- Извлечение текста из PDF, PNG и JPEG документов
- REST API для программной обработки документов
- Результат — чистый Markdown

**База знаний (Вики)**
- Иерархическая вики, привязанная к записям встреч
- Задайте вопрос по любой встрече — AI отвечает и автоматически создаёт две статьи в вики:
  - Родительская: суть встречи (создаётся один раз, переиспользуется)
  - Дочерняя: вопрос пользователя + развёрнутый ответ
- Вопрос также публикуется как комментарий к записи со ссылкой на статью в вики
- Полнотекстовый поиск, версионирование статей, публичные ссылки, слияние статей
- Markdown-редактор с тулбаром

**Организации и пространства**
- Мультитенантность: у каждой организации своё изолированное пространство
- Управление участниками пространства
- Регистрация организации через Telegram-бот (без подтверждения по email)
- Magic-ссылки для входа — один клик, без пароля

**REST API**
- Загрузка документов на OCR и получение результата по URL
- Управление организациями (мастер-ключ)
- UUID API-ключи для каждого пространства

**MCP-сервер**
- `businesspad-mcp` — интегрируйте инструменты BusinessPad напрямую в Claude Desktop, Claude Code или любой MCP-совместимый ИИ-ассистент
- Инструменты: `ocr_submit_url`, `ocr_get_status`, `ocr_extract`, `ocr_list_done`, `org_create`

### Технологический стек

| Уровень | Технология |
|---------|-----------|
| Бэкенд | Django 4.x, Gunicorn |
| База данных | PostgreSQL + pgvector (семантический поиск) |
| Транскрибация | faster-whisper (отдельный микросервис) |
| Хранилище | S3-совместимое объектное хранилище |
| Контейнеризация | Docker + Docker Compose |
| Бот | Telegram Bot API |
| Фронтенд | Vanilla JS, собственный CSS (без фреймворков) |

### Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone https://github.com/neo37/honest-transcription_-_without-sms-and-registration.git
cd honest-transcription_-_without-sms-and-registration

# 2. Настроить окружение
cp .env.example .env
# Отредактируйте .env: SECRET_KEY, параметры БД, S3, токен Telegram, SITE_URL

# 3. Собрать и запустить
docker compose up -d --build

# 4. Открыть платформу
open http://localhost:8000
```

### Переменные окружения

| Переменная | Описание |
|------------|----------|
| `SECRET_KEY` | Секретный ключ Django |
| `DATABASE_URL` | Строка подключения PostgreSQL |
| `S3_ENDPOINT_URL` | Эндпоинт S3-совместимого хранилища |
| `S3_ACCESS_KEY` | Ключ доступа S3 |
| `S3_SECRET_KEY` | Секретный ключ S3 |
| `S3_BUCKET` | Имя бакета S3 |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота |
| `TELEGRAM_BOT_USERNAME` | Имя бота (без @) |
| `SITE_URL` | Публичный URL платформы (нужен для magic-ссылок и вебхуков) |
| `MASTER_API_KEY` | Мастер-ключ для управления организациями через API |
| `DADATA_TOKEN` | Токен DaData (поиск компаний по названию/ИНН) |

### API — краткий справочник

#### OCR (ключ пространства)

```bash
# Отправить документ по URL
curl -X POST https://ваш-домен/api/v1/ocr/ \
  -H "X-Api-Key: ВАШ_UUID_КЛЮЧ" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/doc.pdf", "filename": "doc.pdf"}'
# → {"task_id": 42, "status": "pending"}

# Получить результат
curl https://ваш-домен/api/v1/ocr/42/ \
  -H "X-Api-Key: ВАШ_UUID_КЛЮЧ"
# → {"task_id": 42, "status": "done", "markdown": "..."}

# Список выполненных задач
curl https://ваш-домен/api/space/ВАШ_UUID/ocr/
```

#### Создание организации (мастер-ключ)

```bash
curl -X POST https://ваш-домен/api/v1/org/ \
  -H "X-Api-Key: MASTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Ромашка ООО", "email": "admin@romashka.ru"}'
# → {"org_id": 1, "api_key": "uuid", "password": "...", "magic_link": "https://..."}
```

#### MCP-сервер (интеграция с Claude)

Добавьте в `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "businesspad": {
      "command": "businesspad-mcp",
      "env": {
        "BUSINESSPAD_API_KEY": "ваш-uuid-ключ",
        "BUSINESSPAD_BASE_URL": "https://ваш-домен"
      }
    }
  }
}
```

### Структура проекта

```
.
├── recordings/              # Основное приложение: записи, транскрибация, OCR, auth, API
│   ├── models.py            # Recording, Space, SiteUser, OcrJob, Comment, ...
│   ├── views.py             # Все представления и REST API эндпоинты
│   ├── services.py          # S3-поллер, транскрибация, вызовы LLM
│   ├── telegram_service.py  # Обработчик Telegram-бота
│   ├── s3_client.py         # Загрузка/скачивание из S3
│   └── templates/           # HTML-шаблоны
├── wiki_kb/                 # Приложение базы знаний
│   ├── models.py            # WikiArticle, WikiRevision
│   └── views.py             # CRUD, поиск, слияние, история, шаринг
├── businesspad-mcp/         # MCP-сервер для интеграции с ИИ-ассистентами
├── ocr_server/              # Отдельный микросервис OCR (faster-whisper)
├── docker-compose.yml       # Стек для локальной разработки
└── Dockerfile               # Основной контейнер веб-приложения
```

### Лицензия

Проприетарное программное обеспечение. Все права защищены © BusinessPad.

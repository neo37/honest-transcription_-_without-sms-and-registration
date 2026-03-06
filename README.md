# BusinessPad Baza — Meeting Intelligence for BusinessPad ERP

> Support tooling for [BusinessPad](https://business-pad.com) ERP — automatic meeting transcription, document OCR, and an AI-powered knowledge base built on top of your recordings.

---

## English

### Context

[BusinessPad](https://business-pad.com) is an ERP system for business process management. This project — **BusinessPad Baza** — is a companion service that integrates with the BusinessPad ecosystem and solves a specific pain point: capturing and structuring the knowledge that lives inside meetings, calls, and documents.

Instead of manually writing meeting minutes, team members upload recordings (or connect a meeting room via the BusinessPad Meet integration) and get transcriptions, summaries, and a searchable knowledge base automatically.

### What it does

**Meeting transcription**
- Upload audio/video recordings (MP3, MP4, WAV, M4A, and more)
- Automatic transcription via Whisper (faster-whisper)
- AI-generated title and summary per recording
- Full-text and semantic (vector) search across all transcriptions
- Comments, tags, public share links per recording

**Document OCR**
- Extract text from PDF, PNG, JPEG — output is clean Markdown
- REST API for programmatic document processing
- Results stored per organization space

**AI Knowledge Base (Wiki)**
- Hierarchical wiki linked directly to recordings
- Ask any question about a meeting — the AI creates a structured wiki article:
  - Parent page: meeting summary
  - Child page: question + detailed answer
- The question is also added as a comment on the recording with a link to the wiki article
- Full-text search, article versioning, public share links, article merging
- Markdown editor with toolbar

**Multi-tenant organizations**
- Each organization gets an isolated space with its own recordings, wiki, and API key
- Member management within a space
- Organization onboarding via Telegram bot (no email confirmation required)
- Magic login links — one-click login, no password entry

**REST API**
- OCR submission by URL or file upload
- Organization provisioning (master key)
- Per-space UUID API keys for all operations

**MCP Server**
- `businesspad-mcp` — exposes platform tools to Claude Desktop, Claude Code, and other MCP-compatible AI assistants
- Available tools: `ocr_submit_url`, `ocr_get_status`, `ocr_extract`, `ocr_list_done`, `org_create`

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 4.x, Gunicorn |
| Database | PostgreSQL + pgvector (semantic search) |
| Transcription | faster-whisper (separate microservice) |
| Storage | S3-compatible object storage |
| Containerization | Docker + Docker Compose |
| Bot | Telegram Bot API |
| Frontend | Vanilla JS, custom CSS (no framework) |

### Quick Start

```bash
# 1. Clone
git clone https://github.com/neo37/honest-transcription_-_without-sms-and-registration.git
cd honest-transcription_-_without-sms-and-registration

# 2. Configure
cp .env.example .env
# Edit .env: SECRET_KEY, database, S3, Telegram token, SITE_URL

# 3. Run
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
| `SITE_URL` | Public URL (required for magic links and Telegram webhooks) |
| `MASTER_API_KEY` | Admin key for org provisioning via API |
| `DADATA_TOKEN` | DaData token (Russian company lookup by name/INN) |

### API Reference

#### OCR (space API key)

```bash
# Submit document by URL
curl -X POST https://baza.business-pad.com/api/v1/ocr/ \
  -H "X-Api-Key: YOUR_SPACE_UUID" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/doc.pdf", "filename": "doc.pdf"}'
# → {"task_id": 42, "status": "pending"}

# Poll for result
curl https://baza.business-pad.com/api/v1/ocr/42/ \
  -H "X-Api-Key: YOUR_SPACE_UUID"
# → {"task_id": 42, "status": "done", "markdown": "..."}

# List completed jobs
curl https://baza.business-pad.com/api/space/YOUR_SPACE_UUID/ocr/
```

#### Organization Provisioning (master key)

```bash
curl -X POST https://baza.business-pad.com/api/v1/org/ \
  -H "X-Api-Key: MASTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Corp", "email": "admin@acme.com"}'
# → {"org_id": 1, "api_key": "uuid", "password": "...", "magic_link": "https://..."}
```

#### MCP Server (Claude integration)

```json
{
  "mcpServers": {
    "businesspad": {
      "command": "businesspad-mcp",
      "env": {
        "BUSINESSPAD_API_KEY": "your-space-uuid",
        "BUSINESSPAD_BASE_URL": "https://baza.business-pad.com"
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

### Контекст

[BusinessPad](https://business-pad.com) — ERP-система для управления бизнес-процессами. Этот проект — **BusinessPad Baza** — вспомогательный сервис, который интегрируется в экосистему BusinessPad и решает конкретную задачу: захватить и структурировать знания, которые остаются в записях встреч, звонков и документах.

Вместо того чтобы вручную писать протоколы встреч, сотрудники загружают записи (или подключают переговорную через BusinessPad Meet) и автоматически получают транскрипции, резюме и поисковую базу знаний.

### Что умеет

**Транскрибация встреч**
- Загрузка аудио/видео записей (MP3, MP4, WAV, M4A и др.)
- Автоматическая транскрибация через Whisper (faster-whisper)
- AI-заголовок и резюме для каждой записи
- Полнотекстовый и семантический (векторный) поиск по всем транскрипциям
- Комментарии, теги, публичные ссылки к каждой записи

**OCR документов**
- Извлечение текста из PDF, PNG, JPEG — результат в Markdown
- REST API для программной обработки документов
- Результаты хранятся в пространстве организации

**AI База знаний (Вики)**
- Иерархическая вики, привязанная к записям встреч
- Задайте вопрос по встрече — AI создаёт структурированную статью:
  - Родительская страница: суть встречи (создаётся один раз, переиспользуется)
  - Дочерняя страница: вопрос + развёрнутый ответ
- Вопрос также публикуется как комментарий к записи со ссылкой на статью
- Полнотекстовый поиск, версионирование, публичные ссылки, слияние статей
- Markdown-редактор с тулбаром

**Мультитенантные организации**
- Каждая организация получает изолированное пространство с записями, вики и API-ключом
- Управление участниками внутри пространства
- Подключение организаций через Telegram-бот (без подтверждения по email)
- Magic-ссылки для входа — один клик, без пароля

**REST API**
- OCR по URL или загрузкой файла
- Создание организаций (мастер-ключ)
- UUID API-ключи для каждого пространства

**MCP-сервер**
- `businesspad-mcp` — инструменты платформы доступны напрямую из Claude Desktop, Claude Code и других MCP-совместимых ИИ-ассистентов
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
# 1. Клонировать
git clone https://github.com/neo37/honest-transcription_-_without-sms-and-registration.git
cd honest-transcription_-_without-sms-and-registration

# 2. Настроить окружение
cp .env.example .env
# Отредактируйте .env: SECRET_KEY, параметры БД, S3, токен Telegram, SITE_URL

# 3. Запустить
docker compose up -d --build

# 4. Открыть
open http://localhost:8000
```

### Переменные окружения

| Переменная | Описание |
|------------|----------|
| `SECRET_KEY` | Секретный ключ Django |
| `DATABASE_URL` | Строка подключения PostgreSQL |
| `S3_ENDPOINT_URL` | Эндпоинт S3-хранилища |
| `S3_ACCESS_KEY` | Ключ доступа S3 |
| `S3_SECRET_KEY` | Секретный ключ S3 |
| `S3_BUCKET` | Имя бакета |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота |
| `TELEGRAM_BOT_USERNAME` | Имя бота (без @) |
| `SITE_URL` | Публичный URL (нужен для magic-ссылок и вебхуков) |
| `MASTER_API_KEY` | Мастер-ключ для создания организаций через API |
| `DADATA_TOKEN` | Токен DaData (поиск компаний по названию/ИНН) |

### API — краткий справочник

#### OCR (ключ пространства)

```bash
# Отправить документ по URL
curl -X POST https://baza.business-pad.com/api/v1/ocr/ \
  -H "X-Api-Key: ВАШ_UUID_КЛЮЧ" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/doc.pdf"}'

# Получить результат
curl https://baza.business-pad.com/api/v1/ocr/42/ \
  -H "X-Api-Key: ВАШ_UUID_КЛЮЧ"
```

#### Создание организации (мастер-ключ)

```bash
curl -X POST https://baza.business-pad.com/api/v1/org/ \
  -H "X-Api-Key: MASTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Ромашка ООО", "email": "admin@romashka.ru"}'
```

#### MCP-сервер (интеграция с Claude)

```json
{
  "mcpServers": {
    "businesspad": {
      "command": "businesspad-mcp",
      "env": {
        "BUSINESSPAD_API_KEY": "ваш-uuid-ключ",
        "BUSINESSPAD_BASE_URL": "https://baza.business-pad.com"
      }
    }
  }
}
```

### Структура проекта

```
.
├── recordings/              # Основное приложение: записи, транскрибация, OCR, auth, API
├── wiki_kb/                 # База знаний (вики)
├── businesspad-mcp/         # MCP-сервер для интеграции с ИИ-ассистентами
├── ocr_server/              # Микросервис OCR (faster-whisper)
├── docker-compose.yml       # Стек для разработки
└── Dockerfile               # Контейнер веб-приложения
```

### Лицензия

Проприетарное программное обеспечение. Все права защищены © BusinessPad.

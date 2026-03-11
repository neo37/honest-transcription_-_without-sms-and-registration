# BusinessPad Baza — Meeting Intelligence for BusinessPad ERP

> Companion service for [BusinessPad](https://business-pad.com) ERP — automatic meeting transcription, document OCR, AI voice assistant, and a linked knowledge base built on top of your recordings.

---

## English

### What it does

**Meeting transcription**
- Upload audio/video recordings (MP3, MP4, WAV, M4A, and more) or record directly from a meeting room
- Automatic transcription via WhisperX with speaker diarization (pyannote)
- AI-generated title and summary per recording
- Full-text and semantic (vector) search across all transcriptions
- Comments, tags, public share links per recording

**Document OCR**
- Extract text from PDF, PNG, JPEG — output is clean Markdown
- Multiple backends: Tesseract (CPU), EasyOCR (GPU), olmOCR (cloud)
- REST API for programmatic document processing

**AI Knowledge Base (Wiki)**
- Hierarchical wiki linked directly to recordings
- Ask any question about a meeting — AI creates a structured wiki article
- Full-text search, article versioning, public share links, Markdown editor

**Meeting Rooms (LiveKit)**
- Create and join video/audio meeting rooms
- Optional AI voice assistant ("Маскот") per room: listens, responds, logs activity
- Invite space members via Telegram

**Multi-tenant organizations**
- Each organization gets an isolated space with its own recordings, wiki, and API key
- Member management within a space
- Organization onboarding via Telegram bot
- Magic login links — one-click login, no password entry

**REST API**
- OCR submission by URL or file upload
- Organization provisioning (master key)
- Per-space UUID API keys

### Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 4.x, Gunicorn |
| Database | PostgreSQL + pgvector (semantic search) |
| Transcription | WhisperX + pyannote (speaker diarization) |
| OCR | Tesseract / EasyOCR / olmOCR |
| Voice Agent | LiveKit Agents, Silero TTS/VAD, Whisper STT |
| Storage | S3-compatible object storage |
| Containerization | Docker + Docker Compose |
| Bot | Telegram Bot API |
| Frontend | Vanilla JS, custom CSS |

### Quick Start

```bash
# 1. Clone
git clone <repo-url>
cd meetrec

# 2. Configure
cp .env.example .env
# Edit .env — fill in SECRET_KEY, S3 credentials, Telegram token, SITE_URL

# 3. Run
docker compose up -d --build

# 4. Open
open http://localhost:18000
```

> **Note:** The first startup runs `migrate` and `collectstatic` automatically. Set `DJANGO_SUPERUSER_PASSWORD` in `.env` before first run to create the admin account.

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | ✅ | Django secret key |
| `DATABASE_URL` | ✅ | PostgreSQL connection string (auto-set in Docker) |
| `S3_ENDPOINT_URL` | ✅ | S3-compatible storage endpoint |
| `S3_ACCESS_KEY` | ✅ | S3 access key |
| `S3_SECRET_KEY` | ✅ | S3 secret key |
| `S3_BUCKET` | ✅ | S3 bucket name |
| `SITE_URL` | ✅ | Public URL (required for Telegram webhooks and magic links) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (org registration & invites) |
| `TELEGRAM_BOT_USERNAME` | — | Bot username (without @) |
| `MASTER_API_KEY` | — | Admin key for org provisioning via API |
| `DJANGO_SUPERUSER_PASSWORD` | — | Admin panel password (set before first run) |
| `HUGGINGFACE_TOKEN` | — | HuggingFace token (enables speaker diarization) |
| `LLM_URL` | — | LLM endpoint (OpenAI-compatible) |
| `LLM_AUTH` | — | HTTP auth header for LLM API |
| `LLM_MODEL` | — | Model name (default: `gpt-4.1-mini`) |
| `LIVEKIT_URL` | — | LiveKit server URL |
| `LIVEKIT_API_KEY` | — | LiveKit API key |
| `LIVEKIT_API_SECRET` | — | LiveKit API secret |
| `DADATA_TOKEN` | — | DaData token (Russian company lookup by INN) |
| `BOT_ADMIN_PASSWORD` | — | Password for custom bot management commands |
| `EMAIL_HOST_USER` | — | SMTP username |
| `EMAIL_HOST_PASSWORD` | — | SMTP password |

See `.env.example` for a full list with comments.

### Docker Services

| Service | Description |
|---------|-------------|
| `web` | Main Django application (port 18000) |
| `poller` | Transcription worker — polls S3 queue and runs WhisperX |
| `db` | PostgreSQL 16 with pgvector |
| `ocr` | FastAPI OCR microservice (port 8001) |
| `livekit` | LiveKit media server (port 7880) |
| `voice-agent` | AI voice assistant for meeting rooms |

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
# → {"task_id": 42, "status": "done", "markdown": "..."}
```

#### Organization Provisioning (master key)

```bash
curl -X POST https://your-domain/api/v1/org/ \
  -H "X-Api-Key: MASTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Corp", "email": "admin@acme.com"}'
# → {"org_id": 1, "api_key": "uuid", "password": "...", "magic_link": "https://..."}
```

### Project Structure

```
.
├── recordings/              # Core app: recordings, transcription, OCR, auth, API
│   ├── models.py            # Recording, Space, SiteUser, OcrJob, MeetingRoom, ...
│   ├── views.py             # All views and REST API endpoints
│   ├── services.py          # S3 poller, transcription logic, LLM calls
│   └── telegram_service.py  # Telegram bot handler
├── wiki_kb/                 # Wiki knowledge base app
│   ├── models.py            # WikiArticle, WikiRevision, WikiArticleChunk
│   └── views.py             # CRUD, search, share, history
├── knowledge_graph/         # Entity/relationship graph (experimental)
├── voice_agent/             # LiveKit voice AI assistant
│   ├── agent.py             # Agent entrypoint
│   └── monitor.py           # Room dispatch monitor
├── ocr_server/              # Standalone FastAPI OCR microservice
│   └── app.py               # Tesseract / EasyOCR / olmOCR backends
├── meetrec/                 # Django project settings
├── docker-compose.yml       # Full stack
├── Dockerfile               # Main web/poller container
└── Dockerfile.agent         # Voice agent container
```

---

## Русский

### Контекст

[BusinessPad](https://business-pad.com) — ERP-система для управления бизнес-процессами. Этот проект — **BusinessPad Baza** — вспомогательный сервис, который интегрируется в экосистему BusinessPad: автоматически транскрибирует записи встреч, распознаёт документы (OCR), ведёт базу знаний и обеспечивает голосового ИИ-ассистента для переговорных.

### Что умеет

**Транскрибация встреч**
- Загрузка аудио/видео (MP3, MP4, WAV, M4A и др.) или запись напрямую из переговорной
- Автоматическая транскрибация через WhisperX с диаризацией (определение спикеров)
- AI-заголовок и резюме для каждой записи
- Полнотекстовый и семантический (векторный) поиск
- Комментарии, теги, публичные ссылки к каждой записи

**OCR документов**
- Извлечение текста из PDF, PNG, JPEG — результат в Markdown
- Несколько движков: Tesseract (CPU), EasyOCR (GPU), olmOCR (облако)
- REST API для программной обработки

**AI База знаний (Вики)**
- Иерархическая вики, привязанная к записям
- Задайте вопрос по встрече — AI создаёт структурированную статью
- Полнотекстовый поиск, версионирование, публичные ссылки, Markdown-редактор

**Переговорные комнаты (LiveKit)**
- Создание и подключение к видео/аудио-встречам
- Опциональный голосовой ИИ-ассистент («Маскот») в комнате: слушает, отвечает, логирует
- Приглашение участников через Telegram

**Мультитенантные организации**
- Каждая организация — изолированное пространство с записями, вики и API-ключом
- Управление участниками, onboarding через Telegram-бот
- Magic-ссылки для входа — один клик, без пароля

### Быстрый старт

```bash
# 1. Клонировать
git clone <url-репозитория>
cd meetrec

# 2. Настроить окружение
cp .env.example .env
# Заполните .env: SECRET_KEY, S3, Telegram-токен, SITE_URL

# 3. Запустить
docker compose up -d --build

# 4. Открыть
open http://localhost:18000
```

> **Примечание:** При первом запуске автоматически выполняются `migrate` и `collectstatic`. Задайте `DJANGO_SUPERUSER_PASSWORD` в `.env` до первого запуска.

### Переменные окружения

| Переменная | Обязательная | Описание |
|------------|-------------|----------|
| `SECRET_KEY` | ✅ | Секретный ключ Django |
| `S3_ENDPOINT_URL` | ✅ | Эндпоинт S3-хранилища |
| `S3_ACCESS_KEY` | ✅ | Ключ доступа S3 |
| `S3_SECRET_KEY` | ✅ | Секретный ключ S3 |
| `S3_BUCKET` | ✅ | Имя бакета |
| `SITE_URL` | ✅ | Публичный URL (для вебхуков и magic-ссылок) |
| `TELEGRAM_BOT_TOKEN` | — | Токен Telegram-бота |
| `MASTER_API_KEY` | — | Мастер-ключ для создания организаций через API |
| `DJANGO_SUPERUSER_PASSWORD` | — | Пароль к /admin/ |
| `HUGGINGFACE_TOKEN` | — | Токен HuggingFace (для диаризации спикеров) |
| `LLM_URL` | — | Эндпоинт LLM (OpenAI-совместимый) |
| `LLM_AUTH` | — | HTTP-авторизация для LLM API |
| `LIVEKIT_URL` | — | URL LiveKit-сервера |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | — | Ключи LiveKit |
| `BOT_ADMIN_PASSWORD` | — | Пароль для команд управления ботами |

Полный список — в файле `.env.example`.

### Структура проекта

```
.
├── recordings/              # Основное приложение: записи, транскрибация, OCR, auth, API
├── wiki_kb/                 # База знаний (вики)
├── knowledge_graph/         # Граф сущностей и связей (экспериментально)
├── voice_agent/             # Голосовой ИИ-ассистент (LiveKit)
├── ocr_server/              # Микросервис OCR (FastAPI)
├── meetrec/                 # Настройки Django-проекта
├── docker-compose.yml       # Полный стек
└── Dockerfile               # Контейнер web/poller
```

### Лицензия

Проприетарное программное обеспечение. Все права защищены © BusinessPad.

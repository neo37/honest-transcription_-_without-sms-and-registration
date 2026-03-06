# businesspad-mcp

MCP-сервер для платформы [BusinessPad](https://baza.business-pad.com).
Позволяет Claude и другим MCP-клиентам использовать инструменты BusinessPad напрямую из чата.

## Инструменты

| Tool | Описание |
|------|----------|
| `ocr_submit_url` | Отправить URL документа (PDF/PNG/JPEG) в очередь OCR |
| `ocr_get_status` | Проверить статус задачи и получить Markdown с текстом |
| `ocr_extract` | Отправить и дождаться результата автоматически |
| `ocr_list_done` | Список последних завершённых OCR-задач пространства |
| `org_create` | Создать организацию (требует мастер-ключ) |

## Установка

```bash
pip install businesspad-mcp
```

Или напрямую из репозитория:

```bash
pip install git+https://github.com/business-pad/businesspad-mcp.git
```

## Настройка переменных окружения

| Переменная | Описание |
|------------|----------|
| `BUSINESSPAD_BASE_URL` | URL платформы (по умолчанию `https://baza.business-pad.com`) |
| `BUSINESSPAD_API_KEY` | UUID API-ключ вашего пространства — нужен для OCR-инструментов |
| `BUSINESSPAD_MASTER_KEY` | Мастер-ключ — нужен только для `org_create` |

API-ключ пространства (`BUSINESSPAD_API_KEY`) находится в разделе **Пространство** на платформе.

## Использование с Claude Desktop

Добавьте в `claude_desktop_config.json`:

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

## Использование с Claude Code

```bash
claude mcp add businesspad -- env BUSINESSPAD_API_KEY=ваш-uuid-ключ businesspad-mcp
```

Или через переменную окружения напрямую:

```bash
export BUSINESSPAD_API_KEY=ваш-uuid-ключ
businesspad-mcp
```

## Примеры

### Извлечь текст из PDF одной командой

```
Пользователь: Извлеки текст из https://example.com/contract.pdf

Claude использует ocr_extract → возвращает Markdown с текстом документа
```

### Посмотреть выполненные задачи

```
Пользователь: Покажи последние OCR-задачи нашего пространства

Claude использует ocr_list_done → список с превью текста каждого документа
```

### Создать организацию

```
Пользователь: Создай организацию "Ромашка" с admin@romashka.ru

Claude использует org_create → имя, API-ключ, пароль, magic-ссылка для входа
```

## Требования

- Python 3.10+
- `mcp[cli]` ≥ 1.0.0
- `httpx` ≥ 0.27.0

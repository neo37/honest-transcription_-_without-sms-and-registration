# Image Generator API

FastAPI приложение для генерации изображений по текстовому промпту.

## Установка

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Запуск

```bash
uvicorn app:app --host 0.0.0.0 --port 8001
```

## API

### POST /generate

Генерирует изображение по промпту.

**Request:**
```json
{
  "prompt": "a happy family in a park",
  "negative_prompt": "blurry, low quality"
}
```

**Response:**
```json
{
  "status": "success",
  "image_url": "https://voice.rity.lol/images/uuid.jpg",
  "image_id": "uuid"
}
```


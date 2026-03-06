# OCR-сервис (olmOCR)

Обёртка над [olmOCR](https://github.com/allenai/olmocr): HTTP API для распознавания PDF/изображений в Markdown.

- **POST /ocr** — загрузка файла (multipart, поле `file`), ответ `{"markdown": "..."}`.
- **GET /health** — проверка живости.

Без GPU в контейнере задайте внешний inference в `.env`:

```env
OCR_SERVER=https://api.deepinfra.com/v1/openai
OCR_MODEL=allenai/olmOCR-2-7B-1025
OCR_API_KEY=your_deepinfra_key
```

Провайдеры из [документации olmOCR](https://github.com/allenai/olmocr#verified-external-providers): DeepInfra, Parasail, Cirrascale.
